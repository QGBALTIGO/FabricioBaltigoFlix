from __future__ import annotations

import html
import logging
import math

from sqlalchemy import func, select
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import CallbackContext

from app.bot.keyboards import (
    add_package_help_keyboard,
    filters_keyboard,
    history_keyboard,
    list_keyboard,
    main_menu_keyboard,
    notify_keyboard,
    shipment_keyboard,
)
from app.bot.messages import (
    HELP,
    INVALID_CODE,
    NO_SHIPMENTS,
    SECURITY,
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
from app.status import (
    status_explanation,
    status_label,
)
from app.utils import (
    format_datetime,
    humanize_age,
    is_stale,
    is_valid_tracking_number,
    mentions_payment,
    normalize_tracking_number,
    parse_tracking_input,
)

log = logging.getLogger(__name__)
settings = get_settings()


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

    try:
        member = await context.bot.get_chat_member(
            chat_id=settings.required_channel,
            user_id=user.id,
        )
        if _is_channel_member(member):
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


async def security_cmd(
    update: Update,
    context: CallbackContext,
) -> None:
    await (
        update.effective_message
        .reply_text(
            SECURITY,
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

    pending = context.user_data.get(
        "rename_subscription_id"
    )
    if pending:
        await rename_finish(
            update,
            context,
            text,
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

    number, nickname = parse_tracking_input(
        text
    )
    if not number:
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
) -> None:
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
        return

    if not await _require_channel_membership(
        update,
        context,
    ):
        return

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

            await _edit_tracking_card(
                context,
                msg,
                result.subscription,
                warning=result.warning,
            )

    except ValueError as exc:
        await msg.edit_text(
            "⚠️ "
            + html.escape(
                str(exc)
            )
        )

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


async def search_cmd(
    update: Update,
    context: CallbackContext,
) -> None:
    query = " ".join(
        context.args
    ).strip()

    if not query:
        await (
            update.effective_message
            .reply_text(
                (
                    "🔎 Use "
                    "<code>/buscar TERMO</code>. "
                    "Você pode procurar por "
                    "código, apelido, "
                    "transportadora ou "
                    "texto/status do rastreio."
                ),
                parse_mode=(
                    ParseMode.HTML
                ),
            )
        )
        return

    context.user_data[
        "list_state"
    ] = {
        "mode": "search",
        "query": query,
    }

    await _show_list(
        update,
        context,
        page=0,
    )


async def filters_cmd(
    update: Update,
    context: CallbackContext,
) -> None:
    await _ensure_current_user(
        update,
        context,
    )

    await (
        update.effective_message
        .reply_text(
            (
                "🎛 <b>Filtros inteligentes</b>\n\n"
                "Escolha um status ou filtre "
                "por transportadora. "
                "Você também pode usar "
                "/buscar para combinar "
                "uma busca por texto."
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=(
                filters_keyboard()
            ),
        )
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
        text = (
            f"{title}{suffix}\n\n"
            f"{total} pacote(s) salvo(s). "
            "Toque no pacote que deseja remover."
        )
    else:
        if mode == "active":
            text = _my_packages_text(total)
        else:
            text = (
                f"{title}{suffix}\n\n"
                f"{total} rastreio(s). "
                "Toque em um pacote para "
                "abrir os detalhes."
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

        subs = list(
            (
                await session.scalars(
                    select(
                        Subscription
                    )
                    .join(
                        Shipment,
                        Shipment.id
                        == Subscription
                        .shipment_id,
                    )
                    .where(
                        Subscription.user_id
                        == user.id,
                        Subscription.is_active
                        .is_(True),
                    )
                )
            ).all()
        )

        stale = 0

        for sub in subs:
            shipment = (
                await session.get(
                    Shipment,
                    sub.shipment_id,
                )
            )

            if (
                shipment
                and shipment.status
                != "delivered"
                and is_stale(
                    shipment.last_event_at
                    or shipment
                    .registered_at,
                    settings
                    .stale_after_hours,
                )
            ):
                stale += 1

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
                "⚙️ <b>Preferências de alertas</b>\n\n"
                "Abra um pacote em /meus "
                "e toque em <b>⚙️ Alertas</b>.\n\n"
                "• ⭐ Importantes — recomendado\n"
                "• 🔔 Todos — cada movimentação\n"
                "• 🔕 Sem alertas — somente consulta manual\n\n"
                "⚠️ Também avisamos uma vez "
                "quando um pacote fica "
                f"{days}+ dia(s) sem movimentação, "
                "se o monitor estiver habilitado."
            ),
            parse_mode=ParseMode.HTML,
        )
    )


async def bot_status(
    update: Update,
    context: CallbackContext,
) -> None:
    async with SessionLocal() as session:
        users = await session.scalar(select(func.count(User.id)))
        shipments = await session.scalar(select(func.count(Shipment.id)))
        active = await session.scalar(
            select(func.count(Shipment.id)).where(Shipment.is_active.is_(True))
        )

    providers = []
    if settings.melhor_rastreio_enabled:
        providers.append("Melhor Rastreio GraphQL ✅")
    if settings.direct_fallbacks_enabled:
        providers.append("Fallbacks diretos ✅")
    if settings.seventeen_track_token:
        providers.append("17TRACK opcional ✅")
    if settings.ship24_api_key:
        providers.append("Ship24 opcional ✅")

    if not providers:
        providers.append("Nenhuma fonte ⚠️")

    poller = (
        "✅ inteligente"
        if settings.tracking_poller_enabled
        else "desativado"
    )

    await update.effective_message.reply_text(
        (
            "🟢 <b>"
            f"{html.escape(settings.app_name)}"
            "</b>\n\n"
            f"Fontes: {', '.join(providers)}\n"
            f"Polling: {poller}\n"
            f"Usuários: {users or 0}\n"
            f"Encomendas: {shipments or 0}\n"
            f"Ativas: {active or 0}"
        ),
        parse_mode=ParseMode.HTML,
    )


async def carriers(
    update: Update,
    context: CallbackContext,
) -> None:
    query = " ".join(context.args).strip().lower()

    catalog = [
        ("Correios", "Melhor Rastreio + fallback direto"),
        ("Jadlog", "Melhor Rastreio + fallback direto"),
        ("J&T Express", "Melhor Rastreio"),
        ("Loggi", "Melhor Rastreio"),
        ("LATAM Cargo", "Melhor Rastreio"),
        ("Azul Cargo", "Melhor Rastreio"),
        ("Buslog", "Melhor Rastreio"),
        ("Viação Mundo", "Melhor Rastreio"),
        ("Melhor Envio", "Melhor Rastreio"),
        ("Total Express", "fallback direto"),
    ]

    if query:
        catalog = [
            item
            for item in catalog
            if query in item[0].lower()
        ]

    if not catalog:
        await update.effective_message.reply_text(
            "Nenhuma transportadora encontrada nesse catálogo."
        )
        return

    lines = [
        "🚚 <b>Transportadoras disponíveis</b>",
        "",
    ]
    for name, source in catalog:
        lines.append(
            "• "
            + html.escape(name)
            + " — "
            + html.escape(source)
        )

    lines.extend(
        [
            "",
            "💡 Você não precisa escolher a transportadora na maioria dos casos: "
            "envie o código e o bot tenta as fontes automaticamente.",
        ]
    )

    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
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

    if data == "security":
        await query.message.reply_text(
            SECURITY,
            parse_mode=ParseMode.HTML,
        )
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

    if data.startswith(
        "filterstatus:"
    ):
        status = data.split(
            ":",
            1,
        )[1]

        context.user_data[
            "list_state"
        ] = {
            "mode": "filter",
            "status": status,
        }

        await _show_list(
            update,
            context,
            page=0,
            edit=True,
        )
        return

    if data == "filters:clear":
        context.user_data[
            "list_state"
        ] = {
            "mode": "active"
        }

        await _show_list(
            update,
            context,
            page=0,
            edit=True,
        )
        return

    if data == "filters:carriers":
        async with SessionLocal() as session:
            user = await session.scalar(
                select(User).where(
                    User.telegram_id
                    == update
                    .effective_user.id
                )
            )

            choices = (
                await service(
                    context
                ).carriers_for_user(
                    session,
                    user.id,
                )
                if user
                else []
            )

        context.user_data[
            "carrier_choices"
        ] = choices

        if not choices:
            await (
                query.edit_message_text(
                    "🚚 Você ainda não tem "
                    "transportadoras identificadas "
                    "nos seus rastreios."
                )
            )
            return

        rows = [
            [
                InlineKeyboardButton(
                    name[:45],
                    callback_data=(
                        f"filtercarrier:{i}"
                    ),
                )
            ]
            for i, name in enumerate(
                choices[:25]
            )
        ]

        rows.append(
            [
                InlineKeyboardButton(
                    "🧹 Limpar filtros",
                    callback_data=(
                        "filters:clear"
                    ),
                )
            ]
        )

        await query.edit_message_text(
            (
                "🚚 <b>Filtrar por "
                "transportadora</b>"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=(
                InlineKeyboardMarkup(
                    rows
                )
            ),
        )
        return

    if data.startswith(
        "filtercarrier:"
    ):
        index = int(
            data.split(
                ":",
                1,
            )[1]
        )
        choices = (
            context.user_data.get(
                "carrier_choices"
            )
            or []
        )

        if index >= len(choices):
            await query.answer(
                (
                    "Filtro expirou. "
                    "Use /filtros novamente."
                ),
                show_alert=True,
            )
            return

        context.user_data[
            "list_state"
        ] = {
            "mode": "filter",
            "carrier": choices[
                index
            ],
        }

        await _show_list(
            update,
            context,
            page=0,
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
            await _edit_tracking_card(
                context,
                query.message,
                sub,
            )

        elif action == "refresh":
            try:
                await service(
                    context
                ).refresh_shipment(
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
                    ).refresh_shipment(
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

        elif action == "explain":
            await (
                query.message
                .reply_text(
                    (
                        status_label(
                            sub.shipment.status
                        )
                        + "\n\n"
                        + html.escape(
                            status_explanation(
                                sub.shipment.status
                            )
                        )
                    ),
                    parse_mode=(
                        ParseMode.HTML
                    ),
                )
            )

        elif action == "rename":
            context.user_data[
                "rename_subscription_id"
            ] = sub.id

            await (
                query.message
                .reply_text(
                    "✏️ Envie agora o novo "
                    "nome/apelido dessa encomenda "
                    "(até 120 caracteres)."
                )
            )

        elif action == "alerts":
            await (
                query.message
                .reply_text(
                    (
                        "🔔 <b>Como você quer "
                        "receber as atualizações?</b>"
                    ),
                    parse_mode=(
                        ParseMode.HTML
                    ),
                    reply_markup=(
                        notify_keyboard(
                            sub
                        )
                    ),
                )
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
                    "important"
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

        elif (
            action == "notify"
            and len(parts) >= 3
        ):
            level = parts[2]

            if level not in {
                "important",
                "all",
                "off",
            }:
                return

            sub.notify_level = level
            sub.notifications_enabled = (
                level != "off"
            )

            await session.commit()

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


async def rename_finish(
    update: Update,
    context: CallbackContext,
    text: str,
) -> None:
    sub_id = context.user_data.pop(
        "rename_subscription_id",
        None,
    )

    if not sub_id:
        return

    name = text.strip()[:120]

    if not name:
        await (
            update.effective_message
            .reply_text(
                "Nome inválido."
            )
        )
        return

    async with SessionLocal() as session:
        user = await session.scalar(
            select(User).where(
                User.telegram_id
                == update.effective_user.id
            )
        )

        sub = (
            await service(
                context
            ).get_subscription(
                session,
                user.id,
                sub_id,
            )
            if user
            else None
        )

        if not sub:
            await (
                update.effective_message
                .reply_text(
                    "Rastreio não encontrado."
                )
            )
            return

        sub.nickname = name
        await session.commit()

        await (
            update.effective_message.reply_text(
                "✅ Nome atualizado."
            )
        )
        await _send_tracking_card(
            context,
            update.effective_chat.id,
            sub,
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

    sent = 0

    for chat_id in ids:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
            )
            sent += 1
        except Exception:
            pass

    await (
        update.effective_message
        .reply_text(
            (
                "✅ Broadcast enviado para "
                f"{sent}/{len(ids)} usuários."
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
                "prestar o serviço. Tokens e chaves "
                "ficam apenas no servidor. "
                "CPF/CNPJ não é coletado nesta versão.\n\n"
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
    context.user_data.pop(
        "rename_subscription_id",
        None,
    )
    await (
        update.effective_message
        .reply_text(
            "✅ Operação cancelada."
        )
    )
