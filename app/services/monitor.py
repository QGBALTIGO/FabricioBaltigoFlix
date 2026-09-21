from __future__ import annotations

import asyncio
import logging
from datetime import (
    datetime,
    timedelta,
    timezone,
)

from sqlalchemy import (
    and_,
    or_,
    select,
    text as sql_text,
)
from sqlalchemy.orm import selectinload
from telegram import Bot
from telegram.error import (
    Forbidden,
    TelegramError,
)

from app.config import Settings
from app.database import (
    SessionLocal,
    engine,
)
from app.models import (
    NotificationLog,
    Shipment,
    Subscription,
)
from app.services.notifier import (
    notify_new_events,
    send_due_deferred_notifications,
)
from app.services.tracking import (
    TrackingService,
)
from app.utils import humanize_age

log = logging.getLogger(__name__)


async def _send_stale_alerts(
    bot: Bot,
    settings: Settings,
) -> int:
    sent = 0
    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(
            hours=settings.stale_after_hours
        )
    )

    async with SessionLocal() as session:
        stmt = (
            select(Subscription)
            .options(
                selectinload(
                    Subscription.user
                ),
                selectinload(
                    Subscription.shipment
                ),
            )
            .join(
                Shipment,
                Shipment.id
                == Subscription.shipment_id,
            )
            .where(
                Subscription.is_active.is_(
                    True
                ),
                Subscription.notifications_enabled.is_(
                    True
                ),
                Shipment.is_active.is_(
                    True
                ),
                Shipment.status
                != "delivered",
                or_(
                    Shipment.last_event_at
                    <= cutoff,
                    and_(
                        Shipment.last_event_at
                        .is_(None),
                        Shipment.registered_at
                        <= cutoff,
                    ),
                ),
            )
        )

        subs = list(
            (
                await session.scalars(
                    stmt
                )
            ).all()
        )

        if not subs:
            await session.commit()
            return 0

        sub_ids = [
            sub.id
            for sub in subs
        ]
        existing_rows = (
            await session.execute(
                select(
                    NotificationLog.subscription_id,
                    NotificationLog.dedupe_key,
                ).where(
                    NotificationLog.kind
                    == "stale",
                    NotificationLog.subscription_id
                    .in_(sub_ids),
                )
            )
        ).all()

        existing = {
            (
                int(subscription_id),
                str(dedupe_key),
            )
            for (
                subscription_id,
                dedupe_key,
            ) in existing_rows
        }

        # Libera a conexão do SELECT antes do envio em massa.
        await session.commit()

        pending_logs: list[
            NotificationLog
        ] = []

        for sub in subs:
            shipment = sub.shipment
            reference = (
                shipment.last_event_at
                or shipment.registered_at
            )
            key = (
                shipment.last_event_at
                .isoformat()
                if shipment.last_event_at
                else (
                    "registered:"
                    + shipment.registered_at
                    .isoformat()
                )
            )[:255]

            if (
                sub.id,
                key,
            ) in existing:
                continue

            name = (
                sub.nickname
                or shipment.carrier_name
                or "Sua encomenda"
            )

            text = (
                "⚠️ <b>Encomenda sem nova atualização</b>\n\n"
                f"📦 <b>{name}</b>\n"
                f"🔎 <code>{shipment.tracking_number}</code>\n"
                "🕐 Última movimentação: "
                f"{humanize_age(reference)}\n\n"
                "Isso não significa necessariamente atraso: "
                "algumas etapas não geram leituras. "
                "Se o prazo informado pelo remetente já passou, "
                "consulte a transportadora ou a loja pelos canais oficiais."
            )

            try:
                await bot.send_message(
                    chat_id=(
                        sub.user.telegram_id
                    ),
                    text=text,
                    parse_mode="HTML",
                )
                pending_logs.append(
                    NotificationLog(
                        subscription_id=sub.id,
                        kind="stale",
                        dedupe_key=key,
                    )
                )
                sent += 1
            except Forbidden:
                sub.notifications_enabled = (
                    False
                )
            except TelegramError:
                log.exception(
                    "Falha ao enviar alerta de rastreio parado"
                )

            if (
                settings.notification_send_spacing_seconds
                > 0
            ):
                await asyncio.sleep(
                    settings.notification_send_spacing_seconds
                )

        if pending_logs:
            session.add_all(
                pending_logs
            )

        await session.commit()

    return sent


async def _poll_shipments(
    bot: Bot,
    tracking_service: TrackingService,
    settings: Settings,
) -> tuple[int, int]:
    async with SessionLocal() as session:
        shipments = (
            await tracking_service
            .active_shipments_for_polling(
                session
            )
        )
        shipment_ids = [
            shipment.id
            for shipment in shipments
        ]
        await session.commit()

    if not shipment_ids:
        return 0, 0

    semaphore = asyncio.Semaphore(
        max(
            1,
            settings.poll_concurrency,
        )
    )

    async def process_one(
        shipment_id: int,
    ) -> tuple[int, int]:
        try:
            async with SessionLocal() as session:
                shipment = await session.get(
                    Shipment,
                    shipment_id,
                )

                if (
                    not shipment
                    or not shipment.is_active
                ):
                    return 0, 0

                # O slot limita apenas a consulta externa. O fanout de
                # Telegram e a fila silenciosa não bloqueiam novas consultas.
                async with semaphore:
                    _, new_events = (
                        await tracking_service
                        .refresh_with_events(
                            session,
                            shipment,
                        )
                    )

                    if (
                        settings.poll_request_spacing_seconds
                        > 0
                    ):
                        await asyncio.sleep(
                            settings
                            .poll_request_spacing_seconds
                        )

                notified = 0
                if new_events:
                    notified = (
                        await notify_new_events(
                            session,
                            bot,
                            shipment,
                            new_events,
                        )
                    )

                return 1, notified

        except Exception:
            log.exception(
                "Falha no polling do rastreio %s",
                shipment_id,
            )
            return 0, 0

    results = await asyncio.gather(
        *(
            process_one(
                shipment_id
            )
            for shipment_id
            in shipment_ids
        )
    )

    checked = sum(
        item[0]
        for item in results
    )
    notified = sum(
        item[1]
        for item in results
    )

    return checked, notified


async def _polling_loop(
    bot: Bot,
    tracking_service: TrackingService,
    settings: Settings,
) -> None:
    await asyncio.sleep(10)
    loop = asyncio.get_running_loop()

    while True:
        started = loop.time()

        try:
            checked, notified = (
                await _poll_shipments(
                    bot,
                    tracking_service,
                    settings,
                )
            )

            if checked:
                log.info(
                    "Polling: %s encomenda(s), %s notificação(ões).",
                    checked,
                    notified,
                )

        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(
                "Falha no polling de rastreios"
            )

        interval = max(
            1,
            settings.monitor_tick_minutes,
        ) * 60
        elapsed = (
            loop.time()
            - started
        )

        await asyncio.sleep(
            max(
                1.0,
                interval - elapsed,
            )
        )


async def _stale_loop(
    bot: Bot,
    settings: Settings,
) -> None:
    await asyncio.sleep(30)

    while True:
        try:
            sent = await _send_stale_alerts(
                bot,
                settings,
            )

            if sent:
                log.info(
                    "Alertas de rastreio parado: %s enviado(s).",
                    sent,
                )

        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(
                "Falha no monitor de rastreios parados"
            )

        await asyncio.sleep(
            max(
                10,
                settings
                .stale_check_interval_minutes,
            )
            * 60
        )


async def _deferred_loop(
    bot: Bot,
) -> None:
    await asyncio.sleep(15)

    while True:
        try:
            async with SessionLocal() as session:
                sent = await send_due_deferred_notifications(
                    session,
                    bot,
                    limit=100,
                )
            if sent:
                log.info(
                    "Notificações adiadas entregues: %s.",
                    sent,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(
                "Falha ao entregar notificações adiadas"
            )

        await asyncio.sleep(60)


async def _run_monitor_tasks(
    bot: Bot,
    tracking_service: TrackingService,
    settings: Settings,
) -> None:
    tasks: list[asyncio.Task] = [
        asyncio.create_task(
            _deferred_loop(bot),
            name="deferred-notifications",
        )
    ]

    if settings.tracking_poller_enabled:
        tasks.append(
            asyncio.create_task(
                _polling_loop(
                    bot,
                    tracking_service,
                    settings,
                ),
                name="tracking-poller",
            )
        )

    if settings.stale_monitor_enabled:
        tasks.append(
            asyncio.create_task(
                _stale_loop(
                    bot,
                    settings,
                ),
                name="stale-monitor",
            )
        )

    if not tasks:
        return

    try:
        await asyncio.gather(
            *tasks
        )
    finally:
        for task in tasks:
            task.cancel()

        await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )


async def monitoring_loop(
    bot: Bot,
    tracking_service: TrackingService,
    settings: Settings,
) -> None:
    # Permite várias réplicas web sem multiplicar o poller.
    # O lock é session-level e fica preso a esta conexão.
    is_postgres = (
        settings.database_url.startswith(
            "postgresql://"
        )
        or settings.database_url.startswith(
            "postgres://"
        )
        or settings.database_url.startswith(
            "postgresql+asyncpg://"
        )
    )

    if not is_postgres:
        await _run_monitor_tasks(
            bot,
            tracking_service,
            settings,
        )
        return

    lock_key = 734925817

    while True:
        try:
            async with engine.connect() as conn:
                acquired = bool(
                    await conn.scalar(
                        sql_text(
                            "SELECT pg_try_advisory_lock(:key)"
                        ),
                        {"key": lock_key},
                    )
                )
                await conn.commit()

                if not acquired:
                    log.info(
                        "Monitor em standby; outra réplica é líder."
                    )
                    await asyncio.sleep(30)
                    continue

                log.info(
                    "Monitor automático assumiu liderança."
                )

                try:
                    await _run_monitor_tasks(
                        bot,
                        tracking_service,
                        settings,
                    )
                finally:
                    try:
                        await conn.execute(
                            sql_text(
                                "SELECT pg_advisory_unlock(:key)"
                            ),
                            {"key": lock_key},
                        )
                        await conn.commit()
                    except Exception:
                        log.exception(
                            "Falha ao liberar advisory lock do monitor."
                        )

        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(
                "Falha na eleição do monitor; tentando novamente."
            )
            await asyncio.sleep(30)
