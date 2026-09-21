from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)
from sqlalchemy.orm import selectinload
from telegram import Bot
from telegram.error import (
    Forbidden,
    TelegramError,
)

from app.bot.rich import (
    TelegramRichMessageError,
    send_rich_message,
)
from app.config import get_settings
from app.models import (
    Shipment,
    Subscription,
    TrackingEvent,
)
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
    *,
    new_events_count: int = 1,
) -> str:
    return (
        format_tracking_notification_fallback(
            subscription,
            shipment,
            event,
            settings.display_timezone,
            new_events_count=(
                new_events_count
            ),
        )
    )


async def notify_new_events(
    session: AsyncSession,
    bot: Bot,
    shipment: Shipment,
    events: list[TrackingEvent],
) -> int:
    if not events:
        return 0

    # Histórico guarda tudo; a notificação resume no estado mais recente.
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

    eligible = [
        sub
        for sub in subs
        if should_notify(
            sub.notify_level,
            latest_event.status,
        )
    ]

    if not eligible:
        return 0

    # Libera a conexão de leitura antes do fanout de Telegram.
    await session.commit()

    async def send_one(
        sub: Subscription,
    ) -> bool:
        try:
            try:
                await send_rich_message(
                    settings.telegram_bot_token,
                    sub.user.telegram_id,
                    format_tracking_notification_rich_html(
                        sub,
                        shipment,
                        latest_event,
                        settings.display_timezone,
                        new_events_count=(
                            new_events_count
                        ),
                    ),
                )
            except TelegramRichMessageError:
                await bot.send_message(
                    chat_id=(
                        sub.user.telegram_id
                    ),
                    text=(
                        format_event_notification(
                            sub,
                            shipment,
                            latest_event,
                            new_events_count=(
                                new_events_count
                            ),
                        )
                    ),
                    parse_mode="HTML",
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
        len(eligible),
        batch_size,
    ):
        batch = eligible[
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
            < len(eligible)
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
