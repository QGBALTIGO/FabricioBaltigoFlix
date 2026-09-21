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

MAIN_MENU_TODAY = "🏠 Hoje"
MAIN_MENU_MY_PACKAGES = "📦 Meus pacotes"
MAIN_MENU_REMOVE_PACKAGE = "🗑 Remover pacote"


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [
                KeyboardButton(MAIN_MENU_TODAY),
            ],
            [
                KeyboardButton(MAIN_MENU_MY_PACKAGES),
                KeyboardButton(MAIN_MENU_REMOVE_PACKAGE),
            ],
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
    rows = [
        [
            InlineKeyboardButton(
                "📋 Histórico",
                callback_data=f"history:{sub.id}",
            )
        ],
        [
            InlineKeyboardButton(
                "🔔 Alertas",
                callback_data=f"alertmenu:{sub.id}",
            ),
            InlineKeyboardButton(
                "🧭 O que fazer?",
                callback_data=f"assist:{sub.id}",
            ),
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
                    "📦 Acompanhe esta encomenda comigo no Melhor Rastreio",
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

    return InlineKeyboardMarkup(rows)


def history_keyboard(
    sub_id: int,
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []

        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    "◀️",
                    callback_data=(
                        f"history:{sub_id}:{page - 1}"
                    ),
                )
            )

        nav.append(
            InlineKeyboardButton(
                f"{page + 1}/{total_pages}",
                callback_data="noop",
            )
        )

        if page + 1 < total_pages:
            nav.append(
                InlineKeyboardButton(
                    "▶️",
                    callback_data=(
                        f"history:{sub_id}:{page + 1}"
                    ),
                )
            )

        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                "⬅️ Voltar ao rastreio",
                callback_data=f"back:{sub_id}",
            )
        ]
    )

    return InlineKeyboardMarkup(rows)


def add_package_help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⬅️ Voltar para Meus pacotes",
                    callback_data="packages:back",
                )
            ]
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

    if list_kind == "active":
        rows.append(
            [
                InlineKeyboardButton(
                    "➕ Adicionar encomenda",
                    callback_data="packages:add",
                )
            ]
        )

    return InlineKeyboardMarkup(rows)
