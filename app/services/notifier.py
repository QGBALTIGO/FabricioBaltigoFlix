from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from telegram import Bot
from telegram.error import Forbidden, TelegramError

from app.bot.rich import (
    TelegramRichMessageError,
    send_rich_message,
)
from app.config import get_settings
from app.models import (
    DeferredNotification,
    Shipment,
    Subscription,
    SubscriptionPreference,
    TrackingEvent,
    User,
    UserPreference,
)
from app.presentation import (
    format_tracking_notification_fallback,
    format_tracking_notification_rich_html,
)
from app.services.preferences import (
    custom_alert_allows,
    quiet_window,
)
from app.status import should_notify

settings = get_settings()
log = logging.getLogger(__name__)


def format_event_notification(
    subscription: Subscription,
    shipment: Shipment,
    event: TrackingEvent,
    *,
    new_events_count: int = 1,
) -> str:
    return format_tracking_notification_fallback(
        subscription,
        shipment,
        event,
        settings.display_timezone,
        new_events_count=max(
            1,
            int(new_events_count),
        ),
    )


async def _send_event(
    bot: Bot,
    sub: Subscription,
    shipment: Shipment,
    event: TrackingEvent,
    *,
    telegram_id: int | None = None,
    new_events_count: int = 1,
) -> None:
    target_id = (
        int(telegram_id)
        if telegram_id is not None
        else int(sub.user.telegram_id)
    )
    count = max(
        1,
        int(new_events_count),
    )

    try:
        await send_rich_message(
            settings.telegram_bot_token,
            target_id,
            format_tracking_notification_rich_html(
                sub,
                shipment,
                event,
                settings.display_timezone,
                new_events_count=count,
            ),
        )
    except TelegramRichMessageError:
        await bot.send_message(
            chat_id=target_id,
            text=format_event_notification(
                sub,
                shipment,
                event,
                new_events_count=count,
            ),
            parse_mode="HTML",
        )


async def notify_new_events(
    session: AsyncSession,
    bot: Bot,
    shipment: Shipment,
    events: list[TrackingEvent],
) -> int:
    if not events:
        return 0

    # Histórico guarda todas as movimentações, mas o usuário recebe somente
    # o estado mais recente da rodada, junto com a quantidade agrupada.
    ordered_events = sorted(
        events,
        key=lambda e: e.event_at,
    )
    latest_event = ordered_events[-1]
    new_events_count = len(
        ordered_events
    )

    subs = list(
        (
            await session.scalars(
                select(Subscription)
                .options(
                    selectinload(
                        Subscription.user
                    )
                )
                .where(
                    Subscription.shipment_id
                    == shipment.id,
                    Subscription.is_active.is_(
                        True
                    ),
                    Subscription.notifications_enabled.is_(
                        True
                    ),
                )
            )
        ).all()
    )

    if not subs:
        return 0

    sub_ids = [
        int(sub.id)
        for sub in subs
    ]
    user_ids = [
        int(sub.user_id)
        for sub in subs
    ]

    sub_prefs = {
        int(pref.subscription_id): pref
        for pref in (
            await session.scalars(
                select(
                    SubscriptionPreference
                ).where(
                    SubscriptionPreference.subscription_id
                    .in_(sub_ids)
                )
            )
        ).all()
    }
    user_prefs = {
        int(pref.user_id): pref
        for pref in (
            await session.scalars(
                select(UserPreference).where(
                    UserPreference.user_id.in_(
                        user_ids
                    )
                )
            )
        ).all()
    }

    eligible = [
        sub
        for sub in subs
        if should_notify(
            sub.notify_level,
            latest_event.status,
        )
        and custom_alert_allows(
            sub_prefs.get(int(sub.id)),
            latest_event.status,
        )
    ]

    if not eligible:
        await session.commit()
        return 0

    existing_deferred: set[int] = set()
    latest_event_id = getattr(
        latest_event,
        "id",
        None,
    )
    if latest_event_id:
        existing_deferred = {
            int(subscription_id)
            for subscription_id in (
                await session.scalars(
                    select(
                        DeferredNotification.subscription_id
                    ).where(
                        DeferredNotification.subscription_id
                        .in_(
                            [
                                int(sub.id)
                                for sub in eligible
                            ]
                        ),
                        DeferredNotification.event_id
                        == int(latest_event_id),
                    )
                )
            ).all()
        }

    immediate: list[Subscription] = []

    for sub in eligible:
        quiet, deliver_after = quiet_window(
            user_prefs.get(
                int(sub.user_id)
            ),
            settings.display_timezone,
        )

        if (
            quiet
            and deliver_after is not None
            and latest_event_id
        ):
            if int(sub.id) not in existing_deferred:
                session.add(
                    DeferredNotification(
                        subscription_id=int(
                            sub.id
                        ),
                        event_id=int(
                            latest_event_id
                        ),
                        event_count=max(
                            1,
                            new_events_count,
                        ),
                        deliver_after=deliver_after,
                    )
                )
                existing_deferred.add(
                    int(sub.id)
                )
            continue

        immediate.append(sub)

    # Persiste a fila silenciosa e libera a conexão antes do fanout Telegram.
    await session.commit()

    async def send_one(
        sub: Subscription,
    ) -> bool:
        try:
            await _send_event(
                bot,
                sub,
                shipment,
                latest_event,
                new_events_count=(
                    new_events_count
                ),
            )
            return True

        except Forbidden:
            sub.notifications_enabled = (
                False
            )
            log.info(
                "Usuário %s bloqueou o bot.",
                sub.user.telegram_id,
            )
            return False

        except TelegramError:
            log.exception(
                "Falha ao notificar %s",
                sub.user.telegram_id,
            )
            return False

    sent = 0
    batch_size = max(
        1,
        settings.notification_batch_size,
    )

    for offset in range(
        0,
        len(immediate),
        batch_size,
    ):
        batch = immediate[
            offset:offset + batch_size
        ]

        results = await asyncio.gather(
            *(
                send_one(sub)
                for sub in batch
            )
        )
        sent += sum(
            1
            for result in results
            if result
        )

        if (
            offset + batch_size
            < len(immediate)
            and settings
            .notification_batch_pause_seconds
            > 0
        ):
            await asyncio.sleep(
                settings
                .notification_batch_pause_seconds
            )

    await session.commit()
    return sent


async def send_due_deferred_notifications(
    session: AsyncSession,
    bot: Bot,
    *,
    limit: int = 100,
) -> int:
    now = datetime.now(
        timezone.utc
    )

    rows = (
        await session.execute(
            select(
                DeferredNotification,
                Subscription,
                User,
                Shipment,
                TrackingEvent,
            )
            .join(
                Subscription,
                Subscription.id
                == DeferredNotification.subscription_id,
            )
            .join(
                User,
                User.id
                == Subscription.user_id,
            )
            .join(
                Shipment,
                Shipment.id
                == Subscription.shipment_id,
            )
            .join(
                TrackingEvent,
                TrackingEvent.id
                == DeferredNotification.event_id,
            )
            .where(
                DeferredNotification.deliver_after
                <= now,
            )
            .order_by(
                DeferredNotification.deliver_after.asc(),
                DeferredNotification.id.asc(),
            )
            .limit(
                max(
                    1,
                    int(limit),
                )
            )
        )
    ).all()

    if not rows:
        await session.commit()
        return 0

    sub_ids = [
        int(sub.id)
        for _, sub, _, _, _ in rows
    ]
    user_ids = [
        int(user.id)
        for _, _, user, _, _ in rows
    ]

    sub_prefs = {
        int(pref.subscription_id): pref
        for pref in (
            await session.scalars(
                select(
                    SubscriptionPreference
                ).where(
                    SubscriptionPreference.subscription_id
                    .in_(sub_ids)
                )
            )
        ).all()
    }
    user_prefs = {
        int(pref.user_id): pref
        for pref in (
            await session.scalars(
                select(UserPreference).where(
                    UserPreference.user_id.in_(
                        user_ids
                    )
                )
            )
        ).all()
    }

    await session.commit()

    sent = 0

    for (
        deferred,
        sub,
        user,
        shipment,
        event,
    ) in rows:
        if (
            not sub.is_active
            or not sub.notifications_enabled
            or not should_notify(
                sub.notify_level,
                event.status,
            )
            or not custom_alert_allows(
                sub_prefs.get(
                    int(sub.id)
                ),
                event.status,
            )
        ):
            await session.delete(
                deferred
            )
            continue

        quiet, next_after = quiet_window(
            user_prefs.get(
                int(user.id)
            ),
            settings.display_timezone,
        )
        if (
            quiet
            and next_after is not None
        ):
            deferred.deliver_after = (
                next_after
            )
            continue

        try:
            await _send_event(
                bot,
                sub,
                shipment,
                event,
                telegram_id=(
                    user.telegram_id
                ),
                new_events_count=(
                    deferred.event_count
                    or 1
                ),
            )
            await session.delete(
                deferred
            )
            sent += 1

        except Forbidden:
            sub.notifications_enabled = (
                False
            )
            await session.delete(
                deferred
            )

        except TelegramError:
            log.exception(
                "Falha ao entregar notificação adiada para %s",
                user.telegram_id,
            )
            deferred.deliver_after = (
                datetime.now(
                    timezone.utc
                )
                + timedelta(
                    minutes=5
                )
            )

    await session.commit()
    return sent
