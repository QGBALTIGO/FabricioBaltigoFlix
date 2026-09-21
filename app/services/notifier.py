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
) -> str:
    return format_tracking_notification_fallback(
        subscription,
        shipment,
        event,
        settings.display_timezone,
    )


async def _send_event(
    bot: Bot,
    sub: Subscription,
    shipment: Shipment,
    event: TrackingEvent,
) -> None:
    try:
        await send_rich_message(
            settings.telegram_bot_token,
            sub.user.telegram_id,
            format_tracking_notification_rich_html(
                sub,
                shipment,
                event,
                settings.display_timezone,
            ),
        )
    except TelegramRichMessageError:
        await bot.send_message(
            chat_id=sub.user.telegram_id,
            text=format_event_notification(
                sub,
                shipment,
                event,
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
                    Subscription.shipment_id == shipment.id,
                    Subscription.is_active.is_(True),
                    Subscription.notifications_enabled.is_(True),
                )
            )
        ).all()
    )

    if not subs:
        return 0

    sub_ids = [sub.id for sub in subs]
    user_ids = [sub.user_id for sub in subs]
    event_ids = [
        int(event.id)
        for event in events
        if getattr(event, "id", None)
    ]

    sub_prefs = {
        pref.subscription_id: pref
        for pref in (
            await session.scalars(
                select(SubscriptionPreference).where(
                    SubscriptionPreference.subscription_id.in_(sub_ids)
                )
            )
        ).all()
    }
    user_prefs = {
        pref.user_id: pref
        for pref in (
            await session.scalars(
                select(UserPreference).where(
                    UserPreference.user_id.in_(user_ids)
                )
            )
        ).all()
    }

    existing_deferred: set[tuple[int, int]] = set()
    if event_ids:
        existing_deferred = {
            (int(subscription_id), int(event_id))
            for subscription_id, event_id in (
                await session.execute(
                    select(
                        DeferredNotification.subscription_id,
                        DeferredNotification.event_id,
                    ).where(
                        DeferredNotification.subscription_id.in_(sub_ids),
                        DeferredNotification.event_id.in_(event_ids),
                    )
                )
            ).all()
        }

    # Release the read connection before network I/O.
    await session.commit()

    sent = 0
    pending_deferred: list[DeferredNotification] = []

    for event in sorted(
        events,
        key=lambda e: e.event_at,
    ):
        for sub in subs:
            if not should_notify(
                sub.notify_level,
                event.status,
            ):
                continue

            if not custom_alert_allows(
                sub_prefs.get(sub.id),
                event.status,
            ):
                continue

            quiet, deliver_after = quiet_window(
                user_prefs.get(sub.user_id),
                settings.display_timezone,
            )
            if (
                quiet
                and deliver_after is not None
                and getattr(event, "id", None)
            ):
                key = (int(sub.id), int(event.id))
                if key not in existing_deferred:
                    existing_deferred.add(key)
                    pending_deferred.append(
                        DeferredNotification(
                            subscription_id=sub.id,
                            event_id=int(event.id),
                            deliver_after=deliver_after,
                        )
                    )
                continue

            try:
                await _send_event(
                    bot,
                    sub,
                    shipment,
                    event,
                )
                sent += 1

            except Forbidden:
                sub.notifications_enabled = False
                log.info(
                    "Usuário %s bloqueou o bot.",
                    sub.user.telegram_id,
                )

            except TelegramError:
                log.exception(
                    "Falha ao notificar %s",
                    sub.user.telegram_id,
                )

            if settings.notification_send_spacing_seconds > 0:
                await asyncio.sleep(
                    settings.notification_send_spacing_seconds
                )

    if pending_deferred:
        session.add_all(pending_deferred)

    await session.commit()
    return sent


async def send_due_deferred_notifications(
    session: AsyncSession,
    bot: Bot,
    *,
    limit: int = 100,
) -> int:
    now = datetime.now(timezone.utc)

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
                Subscription.id == DeferredNotification.subscription_id,
            )
            .join(
                User,
                User.id == Subscription.user_id,
            )
            .join(
                Shipment,
                Shipment.id == Subscription.shipment_id,
            )
            .join(
                TrackingEvent,
                TrackingEvent.id == DeferredNotification.event_id,
            )
            .where(
                DeferredNotification.deliver_after <= now,
            )
            .order_by(
                DeferredNotification.deliver_after.asc(),
                DeferredNotification.id.asc(),
            )
            .limit(max(1, int(limit)))
        )
    ).all()

    if not rows:
        await session.commit()
        return 0

    sub_ids = [int(sub.id) for _, sub, _, _, _ in rows]
    user_ids = [int(user.id) for _, _, user, _, _ in rows]

    sub_prefs = {
        pref.subscription_id: pref
        for pref in (
            await session.scalars(
                select(SubscriptionPreference).where(
                    SubscriptionPreference.subscription_id.in_(sub_ids)
                )
            )
        ).all()
    }
    user_prefs = {
        pref.user_id: pref
        for pref in (
            await session.scalars(
                select(UserPreference).where(
                    UserPreference.user_id.in_(user_ids)
                )
            )
        ).all()
    }

    await session.commit()

    sent = 0
    for deferred, sub, user, shipment, event in rows:
        # Attach the already-loaded user object for the existing formatter path.
        sub.user = user

        if (
            not sub.is_active
            or not sub.notifications_enabled
            or not should_notify(sub.notify_level, event.status)
            or not custom_alert_allows(
                sub_prefs.get(sub.id),
                event.status,
            )
        ):
            await session.delete(deferred)
            continue

        quiet, next_after = quiet_window(
            user_prefs.get(user.id),
            settings.display_timezone,
        )
        if quiet and next_after is not None:
            deferred.deliver_after = next_after
            continue

        try:
            await _send_event(
                bot,
                sub,
                shipment,
                event,
            )
            await session.delete(deferred)
            sent += 1

        except Forbidden:
            sub.notifications_enabled = False
            await session.delete(deferred)

        except TelegramError:
            log.exception(
                "Falha ao entregar notificação adiada para %s",
                user.telegram_id,
            )
            deferred.deliver_after = (
                datetime.now(timezone.utc)
                + timedelta(minutes=5)
            )

        if settings.notification_send_spacing_seconds > 0:
            await asyncio.sleep(
                settings.notification_send_spacing_seconds
            )

    await session.commit()
    return sent
