from __future__ import annotations

import json
import logging
from types import SimpleNamespace

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



async def send_admin_notification_previews() -> tuple[int | None, int | None]:
    if not settings.admin_ids:
        log.warning(
            "Prévia de notificações ignorada: nenhum ADMIN_IDS configurado."
        )
        return None, None

    chat_id = sorted(settings.admin_ids)[0]

    sub = SimpleNamespace(
        nickname="Placa 100k",
    )

    real_shipment = SimpleNamespace(
        tracking_number="AP499229999BR",
        carrier_name="Correios",
        extra_json=json.dumps(
            {
                "estimatedDelivery": "2026-10-06",
                "tracking": [
                    {
                        "Posicoes": [
                            {
                                "Acao": (
                                    "Objeto em transferência - por favor aguarde"
                                ),
                                "Data": "2026-09-21 08:49:19",
                                "Detalhes": "",
                                "DetalhesFormatado": (
                                    "Objeto em transferência - por favor aguarde\n\r"
                                    "Saiu de Unidade de Tratamento em CURITIBA / PR "
                                    "para Unidade de Tratamento em CAMPO GRANDE / MS"
                                ),
                            }
                        ]
                    }
                ],
            }
        ),
    )
    real_event = SimpleNamespace(
        status="in_transit",
        event_at="2026-09-21T08:49:19-03:00",
        description=(
            "Objeto em transferência - por favor aguarde"
        ),
        location="Unidade de Tratamento - CURITIBA/PR",
    )

    delivered_shipment = SimpleNamespace(
        tracking_number="AP499229999BR",
        carrier_name="Correios",
        extra_json=json.dumps(
            {
                "trackingEvents": [
                    {
                        "createdAt": "2026-09-22 14:32:00",
                        "description": "Objeto entregue ao destinatário",
                        "from": (
                            "Unidade de Distribuição - CAMPO GRANDE/MS"
                        ),
                        "to": "Destinatário",
                    }
                ],
            }
        ),
    )
    delivered_event = SimpleNamespace(
        status="delivered",
        event_at="2026-09-22T14:32:00-03:00",
        description="Objeto entregue ao destinatário",
        location="CAMPO GRANDE/MS",
    )

    result_1 = await send_rich_message(
        settings.telegram_bot_token,
        chat_id,
        format_tracking_notification_rich_html(
            sub,
            real_shipment,
            real_event,
            settings.display_timezone,
        ),
    )
    result_2 = await send_rich_message(
        settings.telegram_bot_token,
        chat_id,
        format_tracking_notification_rich_html(
            sub,
            delivered_shipment,
            delivered_event,
            settings.display_timezone,
        ),
    )

    message_1 = result_1.get("message_id")
    message_2 = result_2.get("message_id")

    log.info(
        "Admin notification previews sent: real=%s delivered=%s",
        message_1,
        message_2,
    )

    return message_1, message_2
