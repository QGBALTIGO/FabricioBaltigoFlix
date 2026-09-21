from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.models import Subscription


def shipment_keyboard(sub: Subscription) -> InlineKeyboardMarkup:
    mute_text = "🔔 Ativar alertas" if not sub.notifications_enabled else "🔕 Silenciar"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔄 Atualizar", callback_data=f"refresh:{sub.id}"),
                InlineKeyboardButton("📋 Histórico", callback_data=f"history:{sub.id}"),
            ],
            [
                InlineKeyboardButton("✏️ Renomear", callback_data=f"rename:{sub.id}"),
                InlineKeyboardButton(mute_text, callback_data=f"mute:{sub.id}"),
            ],
            [InlineKeyboardButton("🗑 Remover", callback_data=f"delete:{sub.id}")],
        ]
    )


def notify_keyboard(sub: Subscription) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⭐ Importantes", callback_data=f"notify:{sub.id}:important"),
                InlineKeyboardButton("🔔 Todos", callback_data=f"notify:{sub.id}:all"),
            ],
            [InlineKeyboardButton("🔕 Sem alertas", callback_data=f"notify:{sub.id}:off")],
        ]
    )
