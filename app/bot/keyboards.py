from __future__ import annotations

from urllib.parse import quote

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.models import Subscription
from app.share import make_share_payload

MAIN_MENU_MY_PACKAGES = "📦 Meus pacotes"
MAIN_MENU_REMOVE_PACKAGE = "🗑 Remover pacote"


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [
                KeyboardButton(MAIN_MENU_MY_PACKAGES),
                KeyboardButton(MAIN_MENU_REMOVE_PACKAGE),
            ]
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Envie o código de rastreio…",
    )


def shipment_keyboard(
    sub: Subscription,
    bot_username: str | None = None,
    share_secret: str = "",
) -> InlineKeyboardMarkup:
    mute_text = (
        "🔔 Ativar alertas"
        if not sub.notifications_enabled
        else "🔕 Silenciar"
    )
    rows = [
        [
            InlineKeyboardButton(
                "🔄 Atualizar",
                callback_data=f"refresh:{sub.id}",
            ),
            InlineKeyboardButton(
                "📋 Histórico",
                callback_data=f"history:{sub.id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "ℹ️ Entender status",
                callback_data=f"explain:{sub.id}",
            ),
            InlineKeyboardButton(
                "🛡 Segurança",
                callback_data="security",
            ),
        ],
        [
            InlineKeyboardButton(
                "✏️ Renomear",
                callback_data=f"rename:{sub.id}",
            ),
            InlineKeyboardButton(
                "⚙️ Alertas",
                callback_data=f"alerts:{sub.id}",
            ),
        ],
        [
            InlineKeyboardButton(
                mute_text,
                callback_data=f"mute:{sub.id}",
            )
        ],
    ]

    if bot_username and share_secret:
        payload = make_share_payload(
            sub.shipment_id,
            share_secret,
        )
        if payload:
            deep_link = (
                f"https://t.me/{bot_username.lstrip('@')}"
                f"?start={payload}"
            )
            share_url = (
                "https://t.me/share/url?url="
                + quote(deep_link, safe="")
                + "&text="
                + quote(
                    "📦 Acompanhe esta encomenda comigo no Rastreio Baltigo",
                    safe="",
                )
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        "🔗 Compartilhar rastreio",
                        url=share_url,
                    )
                ]
            )

    rows.append(
        [
            InlineKeyboardButton(
                "🗑 Parar de acompanhar",
                callback_data=f"delete:{sub.id}",
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


def notify_keyboard(
    sub: Subscription,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⭐ Importantes",
                    callback_data=(
                        f"notify:{sub.id}:important"
                    ),
                ),
                InlineKeyboardButton(
                    "🔔 Todos",
                    callback_data=(
                        f"notify:{sub.id}:all"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔕 Sem alertas",
                    callback_data=(
                        f"notify:{sub.id}:off"
                    ),
                )
            ],
        ]
    )


def list_keyboard(
    subs: list[Subscription],
    page: int,
    total_pages: int,
    list_kind: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    for sub in subs:
        shipment = sub.shipment
        label = (
            sub.nickname
            or shipment.carrier_name
            or shipment.tracking_number
        )
        emoji = {
            "delivered": "✅",
            "out_for_delivery": "🛵",
            "exception": "❗",
            "delivery_failed": "⚠️",
            "customs": "🛃",
        }.get(
            shipment.status,
            "📦",
        )

        if list_kind == "remove":
            button_text = f"🗑 {label[:40]}"
            callback_data = f"delete:{sub.id}"
        else:
            button_text = f"{emoji} {label[:40]}"
            callback_data = f"open:{sub.id}"

        rows.append(
            [
                InlineKeyboardButton(
                    button_text,
                    callback_data=callback_data,
                )
            ]
        )

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                "⬅️",
                callback_data=(
                    f"page:{list_kind}:{page - 1}"
                ),
            )
        )

    if total_pages > 1:
        nav.append(
            InlineKeyboardButton(
                f"{page + 1}/{total_pages}",
                callback_data="noop",
            )
        )

    if page + 1 < total_pages:
        nav.append(
            InlineKeyboardButton(
                "➡️",
                callback_data=(
                    f"page:{list_kind}:{page + 1}"
                ),
            )
        )

    if nav:
        rows.append(nav)

    return InlineKeyboardMarkup(rows)


def filters_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🚚 Em trânsito",
                    callback_data=(
                        "filterstatus:in_transit"
                    ),
                ),
                InlineKeyboardButton(
                    "🛵 Saiu p/ entrega",
                    callback_data=(
                        "filterstatus:out_for_delivery"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    "⚠️ Problemas",
                    callback_data=(
                        "filterstatus:exception"
                    ),
                ),
                InlineKeyboardButton(
                    "🛃 Alfândega",
                    callback_data=(
                        "filterstatus:customs"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    "✅ Entregues",
                    callback_data=(
                        "filterstatus:delivered"
                    ),
                ),
                InlineKeyboardButton(
                    "🚚 Por transportadora",
                    callback_data=(
                        "filters:carriers"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    "🧹 Limpar filtros",
                    callback_data="filters:clear",
                )
            ],
        ]
    )
