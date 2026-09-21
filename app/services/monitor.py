from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from telegram import Bot
from telegram.error import Forbidden, TelegramError

from app.config import Settings
from app.database import SessionLocal
from app.models import NotificationLog, Shipment, Subscription
from app.services.notifier import notify_new_events
from app.services.tracking import TrackingService
from app.utils import humanize_age, is_stale

log = logging.getLogger(__name__)


async def _send_stale_alerts(
    bot: Bot,
    settings: Settings,
) -> int:
    sent = 0
    async with SessionLocal() as session:
        stmt = (
            select(Subscription)
            .options(
                selectinload(Subscription.user),
                selectinload(Subscription.shipment),
            )
            .join(
                Shipment,
                Shipment.id == Subscription.shipment_id,
            )
            .where(
                Subscription.is_active.is_(True),
                Subscription.notifications_enabled.is_(True),
                Shipment.is_active.is_(True),
                Shipment.status != "delivered",
            )
        )
        subs = list((await session.scalars(stmt)).all())

        for sub in subs:
            shipment = sub.shipment
            reference = (
                shipment.last_event_at
                or shipment.registered_at
            )
            if not is_stale(
                reference,
                settings.stale_after_hours,
            ):
                continue

            key = (
                shipment.last_event_at.isoformat()
                if shipment.last_event_at
                else (
                    "registered:"
                    f"{shipment.registered_at.isoformat()}"
                )
            )
            existing = await session.scalar(
                select(NotificationLog.id).where(
                    NotificationLog.subscription_id
                    == sub.id,
                    NotificationLog.kind == "stale",
                    NotificationLog.dedupe_key
                    == key[:255],
                )
            )
            if existing:
                continue

            log_row = NotificationLog(
                subscription_id=sub.id,
                kind="stale",
                dedupe_key=key[:255],
            )
            session.add(log_row)
            await session.flush()

            name = (
                sub.nickname
                or shipment.carrier_name
                or "Sua encomenda"
            )
            text = (
                "⚠️ <b>Encomenda sem nova atualização</b>\n\n"
                f"📦 <b>{name}</b>\n"
                f"🔎 <code>{shipment.tracking_number}</code>\n"
                f"🕐 Última movimentação: {humanize_age(reference)}\n\n"
                "Isso não significa necessariamente atraso: "
                "algumas etapas não geram leituras. "
                "Se o prazo informado pelo remetente já passou, "
                "consulte a transportadora ou a loja pelos "
                "canais oficiais."
            )
            try:
                await bot.send_message(
                    chat_id=sub.user.telegram_id,
                    text=text,
                    parse_mode="HTML",
                )
                sent += 1
                await session.commit()
            except Forbidden:
                sub.notifications_enabled = False
                await session.commit()
            except TelegramError:
                log.exception(
                    "Falha ao enviar alerta de rastreio parado"
                )
                await session.rollback()
    return sent


async def _poll_shipments(
    bot: Bot,
    tracking_service: TrackingService,
) -> tuple[int, int]:
    checked = 0
    notified = 0
    async with SessionLocal() as session:
        shipments = (
            await tracking_service.active_shipments_for_polling(
                session
            )
        )

    for shipment_stub in shipments:
        try:
            async with SessionLocal() as session:
                shipment = await session.get(
                    Shipment,
                    shipment_stub.id,
                )
                if not shipment or not shipment.is_active:
                    continue
                _, new_events = (
                    await tracking_service.refresh_with_events(
                        session,
                        shipment,
                    )
                )
                checked += 1
                if new_events:
                    notified += await notify_new_events(
                        session,
                        bot,
                        shipment,
                        new_events,
                    )
        except Exception:
            log.exception(
                "Falha no polling do rastreio %s",
                shipment_stub.tracking_number,
            )
    return checked, notified


async def monitoring_loop(
    bot: Bot,
    tracking_service: TrackingService,
    settings: Settings,
) -> None:
    await asyncio.sleep(10)
    last_poll = 0.0
    loop = asyncio.get_running_loop()

    while True:
        now = loop.time()
        try:
            if settings.stale_monitor_enabled:
                await _send_stale_alerts(
                    bot,
                    settings,
                )

            if settings.fallback_poller_enabled:
                interval = (
                    max(
                        15,
                        settings.poll_interval_minutes,
                    )
                    * 60
                )
                if now - last_poll >= interval:
                    await _poll_shipments(
                        bot,
                        tracking_service,
                    )
                    last_poll = now
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(
                "Falha no monitor de rastreios"
            )

        sleep_for = (
            max(
                10,
                settings.stale_check_interval_minutes,
            )
            * 60
        )
        await asyncio.sleep(sleep_for)
