from __future__ import annotations

import logging

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
from app.models import Shipment, Subscription, TrackingEvent
from app.presentation import (
    format_tracking_notification_fallback,
    format_tracking_notification_rich_html,
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
                .options(selectinload(Subscription.user))
                .where(
                    Subscription.shipment_id == shipment.id,
                    Subscription.is_active.is_(True),
                    Subscription.notifications_enabled.is_(True),
                )
            )
        ).all()
    )

    sent = 0

    for event in sorted(events, key=lambda e: e.event_at):
        for sub in subs:
            if not should_notify(
                sub.notify_level,
                event.status,
            ):
                continue

            try:
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

    await session.commit()
    return sent
