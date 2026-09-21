from __future__ import annotations

import asyncio
import html
import logging
import time
from datetime import (
    datetime,
    timedelta,
    timezone,
)
import math

from sqlalchemy import (
    and_,
    func,
    or_,
    select,
)
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import CallbackContext

from app.bot.keyboards import (
    add_package_help_keyboard,
    history_keyboard,
    list_keyboard,
    main_menu_keyboard,
    shipment_keyboard,
)
from app.bot.messages import (
    HELP,
    INVALID_CODE,
    NO_SHIPMENTS,
    WELCOME,
)
from app.bot.rich import (
    TelegramRichMessageError,
    edit_rich_message,
    send_rich_message,
)
from app.config import get_settings
from app.database import SessionLocal
from app.models import (
    Shipment,
    Subscription,
    TrackingEvent,
    User,
)
from app.presentation import (
    format_tracking_card,
    format_tracking_history_fallback_page,
    format_tracking_history_rich_page,
    format_tracking_rich_html,
)
from app.share import verify_share_payload
from app.services.preferences import (
    apply_intake_metadata,
)
from app.status import (
    status_label,
)
from app.utils import (
    humanize_age,
    is_valid_tracking_number,
    normalize_tracking_number,
    parse_tracking_input,
)

log = logging.getLogger(__name__)
settings = get_settings()

_channel_member_cache: dict[int, float] = {}
_channel_member_semaphore = asyncio.Semaphore(
    max(
        1,
        settings.required_channel_check_concurrency,
    )
)


def service(context: CallbackContext):
    return context.application.bot_data[
        "tracking_service"
    ]



def _is_channel_member(chat_member) -> bool:
    status = str(
        getattr(chat_member, "status", "")
        or ""
    ).lower()

    if status in {
        "creator",
        "administrator",
        "member",
    }:
        return True

    if status == "restricted":
        return bool(
            getattr(chat_member, "is_member", False)
        )

    return False


async def _require_channel_membership(
    update: Update,
    context: CallbackContext,
) -> bool:
    if not settings.required_channel_enabled:
        return True

    user = update.effective_user
    if not user:
        return False

    now = time.monotonic()
    cached_until = _channel_member_cache.get(
        user.id,
        0.0,
    )
    if cached_until > now:
        return True

    if len(_channel_member_cache) > 20000:
        expired = [
            telegram_id
            for (
                telegram_id,
                expires_at,
            ) in _channel_member_cache.items()
            if expires_at <= now
        ]
        for telegram_id in expired:
            _channel_member_cache.pop(
                telegram_id,
                None,
            )

    try:
        async with _channel_member_semaphore:
            member = await context.bot.get_chat_member(
                chat_id=settings.required_channel,
                user_id=user.id,
            )
        if _is_channel_member(member):
            _channel_member_cache[
                user.id
            ] = (
                now
                + max(
                    0,
                    settings
                    .required_channel_positive_cache_seconds,
                )
            )
            return True
    except Exception:
        log.exception(
            "Falha ao validar participação no canal obrigatório"
        )
        await update.effective_message.reply_text(
            (
                "⚠️ Não consegui confirmar sua participação "
                "no canal agora. Tente novamente em instantes."
            ),
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "🚀 Abrir Geek Hunter",
                        url=settings.required_channel_url,
                    )
                ]]
            ),
        )
        return False

    name = html.escape(
        user.first_name
        or "por aí"
    )
    channel = html.escape(
        settings.required_channel
    )

    await update.effective_message.reply_text(
        (
            f"Oi, <b>{name}</b>! 🚀\n\n"
            "Este bot é exclusivo para membros do canal "
            f"<b>{channel}</b>.\n\n"
            "Para rastrear sua encomenda, entre no canal "
            "e envie seu código de rastreio novamente 🚚✨."
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "🚀 Entrar no Geek Hunter",
                    url=settings.required_channel_url,
                )
            ]]
        ),
    )
    return False


def _keyboard(
    context: CallbackContext,
    sub: Subscription,
):
    return shipment_keyboard(
        sub,
        bot_username=context.bot.username,
        share_secret=(
            settings.effective_share_secret
        ),
    )


def fmt_shipment(
    sub: Subscription,
) -> str:
    return format_tracking_card(
        sub,
        sub.shipment,
        settings.display_timezone,
    )


def _alert_toggle_toast(enabled: bool) -> str:
    if enabled:
        return (
            "🔔 Alertas ativados! "
            "Vou te avisar sobre novas movimentações."
        )
    return (
        "🔕 Alertas desativados. "
        "Você não receberá novas atualizações."
    )



def _my_packages_text(total: int) -> str:
    if total == 1:
        count = "Você está acompanhando <b>1 encomenda</b>."
    else:
        count = (
            "Você está acompanhando "
            f"<b>{total} encomendas</b>."
        )

    return (
        "📦 <b>Meus pacotes</b>\n\n"
        "<blockquote>"
        + count
        + "</blockquote>\n\n"
        "Toque em um pacote para ver o status, "
        "histórico e alertas."
    )



def _remove_packages_text(total: int) -> str:
    if total == 1:
        count = "Você tem <b>1 pacote salvo</b>."
    else:
        count = (
            "Você tem "
            f"<b>{total} pacotes salvos</b>."
        )

    return (
        "🗑 <b>Remover pacote</b>\n\n"
        "<blockquote>"
        + count
        + "</blockquote>\n\n"
        "Toque no pacote que deseja remover da sua lista."
    )


def _add_package_help_text() -> str:
    return (
        "➕ <b>Adicionar nova encomenda</b>\n\n"
        "Envie o <b>código de rastreio</b> "
        "diretamente no chat.\n\n"
        "<b>Somente o código:</b>\n"
        "<code>AB123456789BR</code>\n\n"
        "<b>Código + nome:</b>\n"
        "<code>AB123456789BR Teclado gamer</code>\n\n"
        "<blockquote>"
        "O nome é opcional e serve apenas para "
        "você identificar o pacote com mais facilidade."
        "</blockquote>\n"
        "Depois de enviar, eu identifico a transportadora "
        "e começo a acompanhar a encomenda automaticamente. 🚚"
    )


async def _edit_tracking_card(
    context: CallbackContext,
    message,
    sub: Subscription,
    *,
    warning: str | None = None,
) -> None:
    markup = _keyboard(
        context,
        sub,
    )
    rich_html = format_tracking_rich_html(
        sub,
        sub.shipment,
        settings.display_timezone,
        warning=warning,
    )

    chat_id = (
        getattr(message, "chat_id", None)
        or message.chat.id
    )

    try:
        await edit_rich_message(
            settings.telegram_bot_token,
            chat_id,
            message.message_id,
            rich_html,
            reply_markup=markup,
        )
    except TelegramRichMessageError:
        log.exception(
            "Falha no Rich Message; usando fallback HTML."
        )
        text = fmt_shipment(sub)
        if warning:
            text += (
                "\n\n⚠️ "
                + html.escape(warning)
            )
        await message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )


async def _send_tracking_card(
    context: CallbackContext,
    chat_id: int,
    sub: Subscription,
) -> None:
    markup = _keyboard(
        context,
        sub,
    )
    rich_html = format_tracking_rich_html(
        sub,
        sub.shipment,
        settings.display_timezone,
    )

    try:
        await send_rich_message(
            settings.telegram_bot_token,
            chat_id,
            rich_html,
            reply_markup=markup,
        )
    except TelegramRichMessageError:
        log.exception(
            "Falha ao enviar Rich Message; usando fallback HTML."
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=fmt_shipment(sub),
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )


async def _ensure_current_user(
    update: Update,
    context: CallbackContext,
):
    async with SessionLocal() as session:
        tg = update.effective_user
        user = await service(
            context
        ).ensure_user(
            session,
            tg.id,
            tg.username,
            tg.first_name,
        )
        await session.commit()
        return user.id


async def start(
    update: Update,
    context: CallbackContext,
) -> None:
    if context.args:
        shipment_id = (
            verify_share_payload(
                context.args[0],
                settings
                .effective_share_secret,
            )
        )

        if shipment_id:
            if not await _require_channel_membership(
                update,
                context,
            ):
                return

            async with SessionLocal() as session:
                tg = (
                    update.effective_user
                )
                user = await service(
                    context
                ).ensure_user(
                    session,
                    tg.id,
                    tg.username,
                    tg.first_name,
                )
                sub = await service(
                    context
                ).follow_existing_shipment(
                    session,
                    user,
                    shipment_id,
                )

                if sub:
                    await (
                        update.effective_message.reply_text(
                            "🔗 <b>Rastreio compartilhado adicionado aos seus pacotes.</b>",
                            parse_mode=ParseMode.HTML,
                        )
                    )
                    await _send_tracking_card(
                        context,
                        update.effective_chat.id,
                        sub,
                    )
                    return

    await _ensure_current_user(
        update,
        context,
    )
    await (
        update.effective_message
        .reply_text(
            WELCOME.format(
                name=html.escape(
                    update.effective_user.first_name
                    or "por aqui"
                )
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard(),
        )
    )


async def help_cmd(
    update: Update,
    context: CallbackContext,
) -> None:
    await (
        update.effective_message
        .reply_text(
            HELP,
            parse_mode=ParseMode.HTML,
        )
    )


async def track_cmd(
    update: Update,
    context: CallbackContext,
) -> None:
    if not context.args:
        await (
            update.effective_message
            .reply_text(
                (
                    "Envie assim: "
                    "<code>/rastrear CODIGO</code>"
                ),
                parse_mode=(
                    ParseMode.HTML
                ),
            )
        )
        return

    raw = " ".join(context.args)
    number, nickname = parse_tracking_input(raw)
    if not number:
        await update.effective_message.reply_text(
            INVALID_CODE,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard(),
        )
        return

    await add_tracking(
        update,
        context,
        number,
        nickname=nickname,
    )


async def text_tracking(
    update: Update,
    context: CallbackContext,
) -> None:
    text = (
        update.effective_message.text
        or ""
    ).strip()

    if text == "🏠 Hoje":
        from app.bot.smart import today_cmd

        await today_cmd(
            update,
            context,
        )
        return

    if text == "📦 Meus pacotes":
        await my_shipments(
            update,
            context,
        )
        return

    if text == "🗑 Remover pacote":
        context.user_data["list_state"] = {
            "mode": "remove"
        }
        await _show_list(
            update,
            context,
            page=0,
        )
        return

    message = update.effective_message
    forwarded = bool(
        getattr(
            message,
            "forward_origin",
            None,
        )
        or getattr(
            message,
            "forward_date",
            None,
        )
    )
    if forwarded:
        from app.bot.smart import (
            offer_smart_candidates,
        )

        offered = await offer_smart_candidates(
            update,
            context,
            text,
            source="forwarded_message",
        )
        if offered:
            return

    number, nickname = parse_tracking_input(
        text
    )
    if not number:
        from app.bot.smart import (
            offer_smart_candidates,
        )

        offered = await offer_smart_candidates(
            update,
            context,
            text,
            source="message",
        )
        if offered:
            return

        await (
            update.effective_message
            .reply_text(
                INVALID_CODE,
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu_keyboard(),
            )
        )
        return

    await add_tracking(
        update,
        context,
        number,
        nickname=nickname,
    )


async def add_tracking(
    update: Update,
    context: CallbackContext,
    raw: str,
    nickname: str | None = None,
    intake_metadata: dict | None = None,
) -> bool:
    number = normalize_tracking_number(
        raw
    )

    if not is_valid_tracking_number(
        number
    ):
        await (
            update.effective_message
            .reply_text(
                INVALID_CODE,
                parse_mode=(
                    ParseMode.HTML
                ),
            )
        )
        return False

    if not await _require_channel_membership(
        update,
        context,
    ):
        return False

    msg = await (
        update.effective_message
        .reply_text(
            (
                "🔎 Consultando "
                f"<code>{html.escape(number)}</code>…"
            ),
            parse_mode=ParseMode.HTML,
        )
    )

    try:
        async with SessionLocal() as session:
            tg = update.effective_user
            user = await service(
                context
            ).ensure_user(
                session,
                tg.id,
                tg.username,
                tg.first_name,
            )

            result = await service(
                context
            ).add_tracking(
                session,
                user,
                number,
                nickname=nickname,
            )

            if intake_metadata:
                await apply_intake_metadata(
                    session,
                    result.subscription.id,
                    store_name=intake_metadata.get(
                        "store_name"
                    ),
                    order_number=intake_metadata.get(
                        "order_number"
                    ),
                    product_name=intake_metadata.get(
                        "product_name"
                    ),
                    source=intake_metadata.get(
                        "source"
                    ),
                )
                await session.commit()

            await _edit_tracking_card(
                context,
                msg,
                result.subscription,
                warning=result.warning,
            )
            return True

    except ValueError as exc:
        await msg.edit_text(
            "⚠️ "
            + html.escape(
                str(exc)
            )
        )
        return False

    except Exception:
        log.exception(
            "Erro ao cadastrar rastreio"
        )
        await msg.edit_text(
            "❌ Não consegui consultar "
            "esse código agora. "
            "Tente novamente em alguns "
            "minutos."
        )
        return False


async def my_shipments(
    update: Update,
    context: CallbackContext,
) -> None:
    context.user_data[
        "list_state"
    ] = {
        "mode": "active"
    }

    await _show_list(
        update,
        context,
        page=0,
    )


async def delivered(
    update: Update,
    context: CallbackContext,
) -> None:
    context.user_data[
        "list_state"
    ] = {
        "mode": "delivered"
    }

    await _show_list(
        update,
        context,
        page=0,
    )


async def _show_list(
    update: Update,
    context: CallbackContext,
    page: int = 0,
    edit: bool = False,
) -> None:
    state = (
        context.user_data.get(
            "list_state"
        )
        or {
            "mode": "active"
        }
    )

    mode = state.get(
        "mode",
        "active",
    )
    delivered_flag: bool | None = False
    status_filter = state.get(
        "status"
    )
    carrier_filter = state.get(
        "carrier"
    )
    query_text = state.get(
        "query"
    )

    if mode == "delivered":
        delivered_flag = True
    elif mode in {
        "search",
        "filter",
        "remove",
    }:
        delivered_flag = None

    async with SessionLocal() as session:
        tg = update.effective_user
        user = await service(
            context
        ).ensure_user(
            session,
            tg.id,
            tg.username,
            tg.first_name,
        )
        await session.commit()

        subs, total = await service(
            context
        ).find_subscriptions(
            session,
            user.id,
            delivered=(
                delivered_flag
            ),
            status=status_filter,
            carrier=carrier_filter,
            query=query_text,
            page=max(0, page),
            page_size=(
                settings.list_page_size
            ),
        )

    if not subs:
        if mode in {
            "search",
            "filter",
        }:
            text = (
                "🔎 Nenhum rastreio "
                "encontrado com esse filtro."
            )
        elif mode == "delivered":
            text = (
                "✅ Você ainda não tem "
                "encomendas entregues salvas."
            )
        elif mode == "remove":
            text = (
                "🗑 Você não tem pacotes salvos para remover."
            )
        else:
            text = (
                "📦 <b>Meus pacotes</b>\n\n"
                "<blockquote>"
                "Você ainda não acompanha nenhuma encomenda."
                "</blockquote>\n"
                "Adicione seu primeiro código de rastreio para começar."
            )

        empty_markup = (
            InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "➕ Adicionar encomenda",
                        callback_data="packages:add",
                    )
                ]]
            )
            if mode == "active"
            else None
        )

        if (
            edit
            and update.callback_query
        ):
            await (
                update.callback_query
                .edit_message_text(
                    text,
                    parse_mode=(
                        ParseMode.HTML
                    ),
                    reply_markup=empty_markup,
                )
            )
        else:
            await (
                update.effective_message
                .reply_text(
                    text,
                    parse_mode=(
                        ParseMode.HTML
                    ),
                    reply_markup=empty_markup,
                )
            )
        return

    total_pages = max(
        1,
        math.ceil(
            total
            / settings.list_page_size
        ),
    )
    page = min(
        page,
        total_pages - 1,
    )

    title = {
        "active": "📦 <b>Meus pacotes</b>",
        "delivered": "✅ <b>Entregues</b>",
        "search": (
            "🔎 <b>Busca:</b> "
            + html.escape(
                query_text or ""
            )
        ),
        "filter": (
            "🎛 <b>Rastreios filtrados</b>"
        ),
        "remove": (
            "🗑 <b>Remover pacote</b>"
        ),
    }.get(
        mode,
        "📦 <b>Rastreios</b>",
    )

    extras = []

    if status_filter:
        extras.append(
            status_label(
                status_filter
            )
        )
    if carrier_filter:
        extras.append(
            "🚚 "
            + html.escape(
                carrier_filter
            )
        )

    suffix = (
        "\n"
        + " • ".join(extras)
        if extras
        else ""
    )

    if mode == "remove":
        text = _remove_packages_text(total)
    else:
        if mode == "active":
            text = _my_packages_text(total)
        else:
            count_text = (
                "<b>1 encomenda</b>"
                if total == 1
                else f"<b>{total} encomendas</b>"
            )
            text = (
                f"{title}{suffix}\n\n"
                f"Você tem {count_text} nesta lista.\n\n"
                "Toque em um pacote para abrir os detalhes."
            )

    markup = list_keyboard(
        subs,
        page,
        total_pages,
        mode,
    )

    if (
        edit
        and update.callback_query
    ):
        await (
            update.callback_query
            .edit_message_text(
                text,
                parse_mode=(
                    ParseMode.HTML
                ),
                reply_markup=markup,
            )
        )
    else:
        await (
            update.effective_message
            .reply_text(
                text,
                parse_mode=(
                    ParseMode.HTML
                ),
                reply_markup=markup,
            )
        )


async def report_cmd(
    update: Update,
    context: CallbackContext,
) -> None:
    async with SessionLocal() as session:
        tg = update.effective_user
        user = await service(
            context
        ).ensure_user(
            session,
            tg.id,
            tg.username,
            tg.first_name,
        )
        await session.commit()

        status_rows = (
            await session.execute(
                select(
                    Shipment.status,
                    func.count(
                        Subscription.id
                    ),
                )
                .join(
                    Subscription,
                    Subscription.shipment_id
                    == Shipment.id,
                )
                .where(
                    Subscription.user_id
                    == user.id,
                    Subscription.is_active.is_(
                        True
                    ),
                )
                .group_by(
                    Shipment.status
                )
            )
        ).all()

        carrier_rows = (
            await session.execute(
                select(
                    Shipment.carrier_name,
                    func.count(
                        Subscription.id
                    ),
                )
                .join(
                    Subscription,
                    Subscription.shipment_id
                    == Shipment.id,
                )
                .where(
                    Subscription.user_id
                    == user.id,
                    Subscription.is_active.is_(
                        True
                    ),
                    Shipment.carrier_name
                    .is_not(None),
                )
                .group_by(
                    Shipment.carrier_name
                )
                .order_by(
                    func.count(
                        Subscription.id
                    ).desc()
                )
                .limit(5)
            )
        ).all()

        stale_cutoff = (
            datetime.now(timezone.utc)
            - timedelta(
                hours=(
                    settings
                    .stale_after_hours
                )
            )
        )

        stale = int(
            await session.scalar(
                select(
                    func.count(
                        Subscription.id
                    )
                )
                .join(
                    Shipment,
                    Shipment.id
                    == Subscription.shipment_id,
                )
                .where(
                    Subscription.user_id
                    == user.id,
                    Subscription.is_active.is_(
                        True
                    ),
                    Shipment.status
                    != "delivered",
                    or_(
                        Shipment.last_event_at
                        <= stale_cutoff,
                        and_(
                            Shipment.last_event_at
                            .is_(None),
                            Shipment.registered_at
                            <= stale_cutoff,
                        ),
                    ),
                )
            )
            or 0
        )

    total = sum(
        int(count)
        for _, count in status_rows
    )

    lines = [
        "📊 <b>Relatório dos seus rastreios</b>",
        "",
        f"📦 Total acompanhados: {total}",
    ]

    for status, count in sorted(
        status_rows,
        key=lambda x: int(x[1]),
        reverse=True,
    ):
        lines.append(
            f"{status_label(status)}: {count}"
        )

    if stale:
        lines.append(
            "⚠️ Sem atualização há "
            f"bastante tempo: {stale}"
        )

    if carrier_rows:
        lines.extend(
            [
                "",
                "🚚 <b>Principais transportadoras</b>",
            ]
        )

        for carrier, count in carrier_rows:
            lines.append(
                "• "
                + html.escape(
                    str(carrier)
                )
                + f": {count}"
            )

    await (
        update.effective_message
        .reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
        )
    )


async def config_cmd(
    update: Update,
    context: CallbackContext,
) -> None:
    days = max(
        1,
        settings.stale_after_hours
        // 24,
    )

    await (
        update.effective_message
        .reply_text(
            (
                "🔔 <b>Alertas de rastreio</b>\n\n"
                "Abra <b>📦 Meus pacotes</b>, toque na encomenda "
                "e use <b>⚙️ Alertas</b> para escolher quais "
                "movimentações quer receber.\n\n"
                "Você pode separar saída para entrega, problemas, "
                "entrega concluída e movimentações intermediárias. "
                "Também há horários silenciosos; nesse período os "
                "avisos ficam guardados e chegam depois.\n\n"
                "⚠️ O bot também pode avisar quando uma encomenda "
                f"fica {days}+ dia(s) sem movimentação."
            ),
            parse_mode=ParseMode.HTML,
        )
    )


async def callback(
    update: Update,
    context: CallbackContext,
) -> None:
    query = update.callback_query
    data = query.data or ""

    # O toggle de alertas responde depois da alteração para exibir
    # um toast nativo do Telegram com o novo estado.
    if not data.startswith("mute:"):
        await query.answer()

    if data == "noop":
        return

    if data == "packages:add":
        await query.edit_message_text(
            _add_package_help_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=add_package_help_keyboard(),
        )
        return

    if data == "packages:back":
        context.user_data["list_state"] = {
            "mode": "active"
        }
        await _show_list(
            update,
            context,
            page=0,
            edit=True,
        )
        return

    if data.startswith(
        "page:"
    ):
        _, mode, raw_page = (
            data.split(
                ":",
                2,
            )
        )
        state = (
            context.user_data.get(
                "list_state"
            )
            or {}
        )
        state["mode"] = mode
        context.user_data[
            "list_state"
        ] = state

        await _show_list(
            update,
            context,
            page=max(
                0,
                int(raw_page),
            ),
            edit=True,
        )
        return

    parts = data.split(":")
    action = parts[0]

    if (
        len(parts) < 2
        or not parts[1].isdigit()
    ):
        return

    sub_id = int(
        parts[1]
    )

    async with SessionLocal() as session:
        user = await session.scalar(
            select(User).where(
                User.telegram_id
                == update.effective_user.id
            )
        )

        if not user:
            await query.answer(
                "Use /start primeiro.",
                show_alert=True,
            )
            return

        sub = await service(
            context
        ).get_subscription(
            session,
            user.id,
            sub_id,
        )

        if not sub:
            await query.answer(
                "Rastreio não encontrado.",
                show_alert=True,
            )
            return

        if action == "open":
            # Opening a package should show fresh data, not only
            # whatever was cached by the last background poll.
            try:
                await service(
                    context
                ).refresh_if_stale(
                    session,
                    sub.shipment,
                )
                sub = await service(
                    context
                ).get_subscription(
                    session,
                    user.id,
                    sub_id,
                )
            except Exception:
                log.exception(
                    "Falha ao atualizar rastreio ao abrir pacote"
                )

            await _edit_tracking_card(
                context,
                query.message,
                sub,
            )

        elif action == "refresh":
            try:
                await service(
                    context
                ).refresh_if_stale(
                    session,
                    sub.shipment,
                    min_age_seconds=0,
                )

                sub = await service(
                    context
                ).get_subscription(
                    session,
                    user.id,
                    sub_id,
                )

                await _edit_tracking_card(
                    context,
                    query.message,
                    sub,
                )

            except Exception:
                log.exception(
                    "Erro ao atualizar"
                )
                await query.answer(
                    "Não foi possível "
                    "atualizar agora.",
                    show_alert=True,
                )

        elif action == "back":
            await _edit_tracking_card(
                context,
                query.message,
                sub,
            )

        elif action == "history":
            page = 0
            if (
                len(parts) >= 3
                and parts[2].isdigit()
            ):
                page = int(parts[2])

            # Only refresh when entering history. Pagination should be instant.
            if page == 0:
                try:
                    await service(
                        context
                    ).refresh_if_stale(
                        session,
                        sub.shipment,
                    )
                    sub = await service(
                        context
                    ).get_subscription(
                        session,
                        user.id,
                        sub_id,
                    )
                except Exception:
                    log.exception(
                        "Falha ao atualizar antes do histórico"
                    )

            events = await service(
                context
            ).history(
                session,
                sub.shipment_id,
                50,
            )

            if not events:
                await query.answer(
                    "Ainda não há eventos no histórico.",
                    show_alert=True,
                )
                return

            rich_html, page, total_pages = (
                format_tracking_history_rich_page(
                    sub,
                    sub.shipment,
                    events,
                    settings.display_timezone,
                    page=page,
                    events_per_page=3,
                )
            )
            markup = history_keyboard(
                sub.id,
                page,
                total_pages,
            )

            try:
                await edit_rich_message(
                    settings.telegram_bot_token,
                    query.message.chat_id,
                    query.message.message_id,
                    rich_html,
                    reply_markup=markup,
                )
            except TelegramRichMessageError:
                log.exception(
                    "Falha no histórico Rich Message; usando fallback HTML."
                )
                fallback, page, total_pages = (
                    format_tracking_history_fallback_page(
                        sub,
                        sub.shipment,
                        events,
                        settings.display_timezone,
                        page=page,
                        events_per_page=3,
                    )
                )
                await query.edit_message_text(
                    fallback,
                    parse_mode=ParseMode.HTML,
                    reply_markup=history_keyboard(
                        sub.id,
                        page,
                        total_pages,
                    ),
                )




        elif action == "mute":
            sub.notifications_enabled = (
                not sub
                .notifications_enabled
            )

            if (
                sub.notifications_enabled
                and sub.notify_level
                == "off"
            ):
                sub.notify_level = (
                    "all"
                )

            await session.commit()

            await query.answer(
                _alert_toggle_toast(
                    sub.notifications_enabled
                )
            )

            await _edit_tracking_card(
                context,
                query.message,
                sub,
            )


        elif action == "delete":
            sub.is_active = False
            sub.notifications_enabled = (
                False
            )

            await session.commit()

            await query.edit_message_text(
                (
                    "🗑 <b>Pacote removido.</b>\n"
                    "<code>"
                    f"{html.escape(sub.shipment.tracking_number)}"
                    "</code>"
                ),
                parse_mode=(
                    ParseMode.HTML
                ),
            )


async def admin(
    update: Update,
    context: CallbackContext,
) -> None:
    if (
        update.effective_user.id
        not in settings.admin_ids
    ):
        return

    async with SessionLocal() as session:
        users = await session.scalar(
            select(
                func.count(User.id)
            )
        )
        shipments = await session.scalar(
            select(
                func.count(
                    Shipment.id
                )
            )
        )
        active = await session.scalar(
            select(
                func.count(
                    Shipment.id
                )
            ).where(
                Shipment.is_active.is_(
                    True
                )
            )
        )
        events = await session.scalar(
            select(
                func.count(
                    TrackingEvent.id
                )
            )
        )

        top = (
            await session.execute(
                select(
                    Shipment.carrier_name,
                    func.count(
                        Shipment.id
                    ),
                )
                .where(
                    Shipment.carrier_name
                    .is_not(None)
                )
                .group_by(
                    Shipment.carrier_name
                )
                .order_by(
                    func.count(
                        Shipment.id
                    ).desc()
                )
                .limit(5)
            )
        ).all()

    lines = [
        "🛠 <b>Admin</b>",
        "",
        f"👥 Usuários: {users or 0}",
        (
            "📦 Encomendas: "
            f"{shipments or 0}"
        ),
        f"🚚 Ativas: {active or 0}",
        (
            "🔔 Eventos armazenados: "
            f"{events or 0}"
        ),
    ]

    if top:
        lines.extend(
            [
                "",
                "<b>Top transportadoras</b>",
            ]
        )

        lines.extend(
            (
                "• "
                + html.escape(
                    str(name)
                )
                + f": {count}"
            )
            for name, count in top
        )

    await (
        update.effective_message
        .reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
        )
    )


async def _broadcast_worker(
    bot,
    ids: list[int],
    text: str,
    admin_chat_id: int,
) -> None:
    sent = 0
    failed = 0
    batch_size = max(
        1,
        settings.broadcast_batch_size,
    )

    for offset in range(
        0,
        len(ids),
        batch_size,
    ):
        batch = ids[
            offset:offset + batch_size
        ]

        async def send_one(
            chat_id: int,
        ) -> bool:
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                )
                return True
            except Exception:
                return False

        results = await asyncio.gather(
            *(
                send_one(chat_id)
                for chat_id in batch
            )
        )

        sent += sum(
            1
            for result in results
            if result
        )
        failed += (
            len(results)
            - sum(
                1
                for result in results
                if result
            )
        )

        if (
            offset + batch_size
            < len(ids)
            and settings
            .broadcast_batch_pause_seconds
            > 0
        ):
            await asyncio.sleep(
                settings
                .broadcast_batch_pause_seconds
            )

    try:
        await bot.send_message(
            chat_id=admin_chat_id,
            text=(
                "✅ Broadcast concluído.\n"
                f"Enviados: {sent}\n"
                f"Falhas: {failed}"
            ),
        )
    except Exception:
        log.exception(
            "Falha ao enviar resumo do broadcast"
        )


async def broadcast(
    update: Update,
    context: CallbackContext,
) -> None:
    if (
        update.effective_user.id
        not in settings.admin_ids
    ):
        return

    text = " ".join(
        context.args
    ).strip()

    if not text:
        await (
            update.effective_message
            .reply_text(
                "Use: /broadcast sua mensagem"
            )
        )
        return

    async with SessionLocal() as session:
        ids = list(
            (
                await session.scalars(
                    select(
                        User.telegram_id
                    ).where(
                        User.is_blocked
                        .is_(False)
                    )
                )
            ).all()
        )

    context.application.create_task(
        _broadcast_worker(
            context.bot,
            ids,
            text,
            update.effective_chat.id,
        ),
        update=update,
        name="admin-broadcast",
    )

    await (
        update.effective_message
        .reply_text(
            (
                "🚀 Broadcast iniciado em segundo plano "
                f"para {len(ids)} usuário(s)."
            )
        )
    )


async def privacy_cmd(
    update: Update,
    context: CallbackContext,
) -> None:
    await (
        update.effective_message
        .reply_text(
            (
                "🔐 <b>Privacidade</b>\n\n"
                "O bot guarda seu ID do Telegram, "
                "códigos cadastrados, apelidos, "
                "preferências de alerta e eventos "
                "de rastreio necessários para "
                "prestar o serviço. Quando você confirma "
                "um código encontrado em mensagem ou print, "
                "o bot também pode guardar loja, número do "
                "pedido e nome do produto detectados para "
                "organizar a encomenda.\n\n"
                "📸 Prints são processados pelo OCR no próprio "
                "servidor do bot e a imagem não é gravada no "
                "banco nem mantida como arquivo da aplicação "
                "após a leitura. Tokens e chaves ficam apenas "
                "no servidor. CPF/CNPJ não é coletado nesta versão.\n\n"
                "Links compartilháveis usam uma "
                "assinatura para impedir a criação "
                "manual de convites para outros "
                "rastreios.\n\n"
                "Para excluir um código salvo, use "
                "o botão 🗑 Remover pacote no menu principal."
            ),
            parse_mode=ParseMode.HTML,
        )
    )


async def cancel_cmd(
    update: Update,
    context: CallbackContext,
) -> None:
    await (
        update.effective_message
        .reply_text(
            "✅ Operação cancelada."
        )
    )
