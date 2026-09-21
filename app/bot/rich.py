from __future__ import annotations

from typing import Any

import httpx


class TelegramRichMessageError(RuntimeError):
    pass


def _markup_payload(reply_markup: Any | None) -> Any | None:
    if reply_markup is None:
        return None
    if hasattr(reply_markup, "to_dict"):
        return reply_markup.to_dict()
    return reply_markup


async def _bot_api(
    token: str,
    method: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if not token:
        raise TelegramRichMessageError(
            "Token do Telegram não configurado."
        )

    url = f"https://api.telegram.org/bot{token}/{method}"

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            response = await client.post(url, json=payload)
    except httpx.HTTPError as exc:
        # Não inclui a URL na exceção para evitar vazar o token em logs.
        raise TelegramRichMessageError(
            "Falha de rede na Bot API."
        ) from exc

    try:
        body = response.json()
    except ValueError as exc:
        raise TelegramRichMessageError(
            "Resposta inválida da Bot API."
        ) from exc

    if not body.get("ok"):
        description = str(
            body.get("description")
            or "Falha ao enviar Rich Message."
        )
        raise TelegramRichMessageError(
            description[:300]
        )

    result = body.get("result")
    return result if isinstance(result, dict) else {}


async def send_rich_message(
    token: str,
    chat_id: int | str,
    rich_html: str,
    *,
    reply_markup: Any | None = None,
    disable_notification: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "rich_message": {
            "html": rich_html,
            "skip_entity_detection": True,
        },
        "disable_notification": disable_notification,
    }

    markup = _markup_payload(reply_markup)
    if markup is not None:
        payload["reply_markup"] = markup

    return await _bot_api(
        token,
        "sendRichMessage",
        payload,
    )


async def edit_rich_message(
    token: str,
    chat_id: int | str,
    message_id: int,
    rich_html: str,
    *,
    reply_markup: Any | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "rich_message": {
            "html": rich_html,
            "skip_entity_detection": True,
        },
    }

    markup = _markup_payload(reply_markup)
    if markup is not None:
        payload["reply_markup"] = markup

    return await _bot_api(
        token,
        "editMessageText",
        payload,
    )
