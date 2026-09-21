from __future__ import annotations

import logging

from sqlalchemy import func, select
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext

from app.bot.keyboards import shipment_keyboard
from app.bot.messages import HELP, INVALID_CODE, NO_SHIPMENTS, WELCOME
from app.config import get_settings
from app.database import SessionLocal
from app.models import Shipment, TrackingEvent, User
from app.status import status_label
from app.utils import format_datetime, is_valid_tracking_number, normalize_tracking_number

log = logging.getLogger(__name__)
settings = get_settings()


def service(context: CallbackContext):
    return context.application.bot_data["tracking_service"]


def fmt_shipment(sub) -> str:
    s = sub.shipment
    name = sub.nickname or s.carrier_name or "Encomenda"
    lines = [
        f"📦 <b>{name}</b>",
        f"🔎 <code>{s.tracking_number}</code>",
        f"🚚 {s.carrier_name or 'Transportadora em detecção'}",
        f"{status_label(s.status)}",
    ]
    if s.last_description:
        lines.append(f"📝 {s.last_description}")
    if s.last_location:
        lines.append(f"📍 {s.last_location}")
    if s.last_event_at:
        lines.append(f"🕐 {format_datetime(s.last_event_at, settings.display_timezone)}")
    if not sub.notifications_enabled or sub.notify_level == "off":
        lines.append("🔕 Alertas desativados")
    elif sub.notify_level == "all":
        lines.append("🔔 Alertas: todas as movimentações")
    else:
        lines.append("⭐ Alertas: movimentações importantes")
    return "\n".join(lines)


async def start(update: Update, context: CallbackContext) -> None:
    async with SessionLocal() as session:
        tg = update.effective_user
        await service(context).ensure_user(session, tg.id, tg.username, tg.first_name)
        await session.commit()
    await update.effective_message.reply_text(WELCOME, parse_mode=ParseMode.HTML)


async def help_cmd(update: Update, context: CallbackContext) -> None:
    await update.effective_message.reply_text(HELP, parse_mode=ParseMode.HTML)


async def track_cmd(update: Update, context: CallbackContext) -> None:
    if not context.args:
        await update.effective_message.reply_text("Envie assim: <code>/rastrear CODIGO</code>", parse_mode=ParseMode.HTML)
        return
    await add_tracking(update, context, context.args[0])


async def text_tracking(update: Update, context: CallbackContext) -> None:
    text = (update.effective_message.text or "").strip()
    pending = context.user_data.get("rename_subscription_id")
    if pending:
        await rename_finish(update, context, text)
        return
    if not is_valid_tracking_number(text):
        await update.effective_message.reply_text(INVALID_CODE, parse_mode=ParseMode.HTML)
        return
    await add_tracking(update, context, text)


async def add_tracking(update: Update, context: CallbackContext, raw: str) -> None:
    number = normalize_tracking_number(raw)
    if not is_valid_tracking_number(number):
        await update.effective_message.reply_text(INVALID_CODE, parse_mode=ParseMode.HTML)
        return

    msg = await update.effective_message.reply_text(
        f"🔎 Consultando <code>{number}</code>…",
        parse_mode=ParseMode.HTML,
    )
    try:
        async with SessionLocal() as session:
            tg = update.effective_user
            user = await service(context).ensure_user(session, tg.id, tg.username, tg.first_name)
            result = await service(context).add_tracking(session, user, number)
            text = fmt_shipment(result.subscription)
            if result.warning:
                text += f"\n\n⚠️ {result.warning}"
            await msg.edit_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=shipment_keyboard(result.subscription),
            )
    except ValueError as exc:
        await msg.edit_text(f"⚠️ {exc}")
    except Exception:
        log.exception("Erro ao cadastrar rastreio")
        await msg.edit_text("❌ Não consegui consultar esse código agora. Tente novamente em alguns minutos.")


async def my_shipments(update: Update, context: CallbackContext) -> None:
    await list_shipments(update, context, delivered=False)


async def delivered(update: Update, context: CallbackContext) -> None:
    await list_shipments(update, context, delivered=True)


async def list_shipments(update: Update, context: CallbackContext, delivered: bool) -> None:
    async with SessionLocal() as session:
        tg = update.effective_user
        user = await service(context).ensure_user(session, tg.id, tg.username, tg.first_name)
        await session.commit()
        subs = await service(context).list_subscriptions(session, user.id, delivered=delivered)
        if not subs:
            await update.effective_message.reply_text(
                "✅ Você ainda não tem encomendas entregues salvas." if delivered else NO_SHIPMENTS,
                parse_mode=ParseMode.HTML,
            )
            return
        title = "✅ <b>Entregues</b>" if delivered else "📦 <b>Meus pacotes</b>"
        await update.effective_message.reply_text(title, parse_mode=ParseMode.HTML)
        for sub in subs[:30]:
            await update.effective_message.reply_text(
                fmt_shipment(sub),
                parse_mode=ParseMode.HTML,
                reply_markup=shipment_keyboard(sub),
            )


async def config_cmd(update: Update, context: CallbackContext) -> None:
    await update.effective_message.reply_text(
        "⚙️ <b>Preferências</b>\n\nEscolha os alertas individualmente em cada pacote em /meus.\n"
        "• ⭐ Importantes — recomendado\n• 🔔 Todos — cada movimentação\n• 🔕 Sem alertas — somente consulta manual",
        parse_mode=ParseMode.HTML,
    )


async def bot_status(update: Update, context: CallbackContext) -> None:
    async with SessionLocal() as session:
        users = await session.scalar(select(func.count(User.id)))
        shipments = await session.scalar(select(func.count(Shipment.id)))
        active = await session.scalar(select(func.count(Shipment.id)).where(Shipment.is_active.is_(True)))
    providers = []
    if settings.seventeen_track_token:
        providers.append("17TRACK ✅")
    if settings.ship24_api_key:
        providers.append("Ship24 ✅")
    if not providers:
        providers.append("Nenhum provedor ⚠️")
    await update.effective_message.reply_text(
        f"🟢 <b>{settings.app_name}</b>\n\n"
        f"Provedores: {', '.join(providers)}\n"
        f"Usuários: {users or 0}\n"
        f"Encomendas: {shipments or 0}\n"
        f"Ativas: {active or 0}",
        parse_mode=ParseMode.HTML,
    )


async def carriers(update: Update, context: CallbackContext) -> None:
    query = " ".join(context.args).strip()
    ship24 = service(context).ship24
    if not query:
        await update.effective_message.reply_text(
            "🚚 O bot usa autodetecção e não exige que você escolha a transportadora.\n\n"
            "Para procurar uma, use <code>/transportadoras jadlog</code>.",
            parse_mode=ParseMode.HTML,
        )
        return
    if not ship24:
        await update.effective_message.reply_text(
            "🔎 A pesquisa de catálogo exige a chave Ship24. O rastreamento automático continua disponível pelo provedor configurado."
        )
        return
    try:
        items = await ship24.search_carriers(query)
        if not items:
            await update.effective_message.reply_text("Nenhuma transportadora encontrada.")
            return
        lines = ["🚚 <b>Transportadoras encontradas</b>", ""]
        for c in items[:15]:
            name = c.get("courierName") or c.get("name") or "Transportadora"
            code = c.get("courierCode") or c.get("code") or ""
            lines.append(f"• {name} <code>{code}</code>")
        await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception:
        log.exception("Erro ao pesquisar transportadoras")
        await update.effective_message.reply_text("❌ Não consegui pesquisar as transportadoras agora.")


async def callback(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()
    parts = (query.data or "").split(":")
    action = parts[0]
    if len(parts) < 2 or not parts[1].isdigit():
        return
    sub_id = int(parts[1])

    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_id == update.effective_user.id))
        if not user:
            await query.answer("Use /start primeiro.", show_alert=True)
            return
        sub = await service(context).get_subscription(session, user.id, sub_id)
        if not sub:
            await query.answer("Rastreio não encontrado.", show_alert=True)
            return

        if action == "refresh":
            try:
                await service(context).refresh_shipment(session, sub.shipment)
                sub = await service(context).get_subscription(session, user.id, sub_id)
                await query.edit_message_text(
                    fmt_shipment(sub),
                    parse_mode=ParseMode.HTML,
                    reply_markup=shipment_keyboard(sub),
                )
            except Exception:
                log.exception("Erro ao atualizar")
                await query.answer("Não foi possível atualizar agora.", show_alert=True)

        elif action == "history":
            events = await service(context).history(session, sub.shipment_id, 20)
            if not events:
                await query.answer("Ainda não há eventos no histórico.", show_alert=True)
                return
            lines = [f"📋 <b>Histórico</b>\n<code>{sub.shipment.tracking_number}</code>", ""]
            for ev in events:
                lines.append(
                    f"{status_label(ev.status)}\n"
                    f"{format_datetime(ev.event_at, settings.display_timezone)} — {ev.description}"
                    + (f"\n📍 {ev.location}" if ev.location else "")
                    + "\n"
                )
            await query.message.reply_text("\n".join(lines)[:3900], parse_mode=ParseMode.HTML)

        elif action == "rename":
            context.user_data["rename_subscription_id"] = sub.id
            await query.message.reply_text("✏️ Envie agora o novo nome/apelido dessa encomenda (até 120 caracteres).")

        elif action == "mute":
            sub.notifications_enabled = not sub.notifications_enabled
            if sub.notifications_enabled and sub.notify_level == "off":
                sub.notify_level = "important"
            await session.commit()
            await query.edit_message_text(
                fmt_shipment(sub),
                parse_mode=ParseMode.HTML,
                reply_markup=shipment_keyboard(sub),
            )

        elif action == "notify" and len(parts) >= 3:
            level = parts[2]
            if level not in {"important", "all", "off"}:
                return
            sub.notify_level = level
            sub.notifications_enabled = level != "off"
            await session.commit()
            await query.edit_message_text(
                fmt_shipment(sub),
                parse_mode=ParseMode.HTML,
                reply_markup=shipment_keyboard(sub),
            )

        elif action == "delete":
            sub.is_active = False
            sub.notifications_enabled = False
            await session.commit()
            await query.edit_message_text(
                f"🗑 Rastreio removido.\n<code>{sub.shipment.tracking_number}</code>",
                parse_mode=ParseMode.HTML,
            )


async def rename_finish(update: Update, context: CallbackContext, text: str) -> None:
    sub_id = context.user_data.pop("rename_subscription_id", None)
    if not sub_id:
        return
    name = text.strip()[:120]
    if not name:
        await update.effective_message.reply_text("Nome inválido.")
        return
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_id == update.effective_user.id))
        sub = await service(context).get_subscription(session, user.id, sub_id) if user else None
        if not sub:
            await update.effective_message.reply_text("Rastreio não encontrado.")
            return
        sub.nickname = name
        await session.commit()
        await update.effective_message.reply_text(
            "✅ Nome atualizado.\n\n" + fmt_shipment(sub),
            parse_mode=ParseMode.HTML,
            reply_markup=shipment_keyboard(sub),
        )


async def admin(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id not in settings.admin_ids:
        return
    async with SessionLocal() as session:
        users = await session.scalar(select(func.count(User.id)))
        shipments = await session.scalar(select(func.count(Shipment.id)))
        active = await session.scalar(select(func.count(Shipment.id)).where(Shipment.is_active.is_(True)))
        events = await session.scalar(select(func.count(TrackingEvent.id)))
    await update.effective_message.reply_text(
        f"🛠 <b>Admin</b>\n\n👥 Usuários: {users or 0}\n📦 Encomendas: {shipments or 0}\n"
        f"🚚 Ativas: {active or 0}\n🔔 Eventos armazenados: {events or 0}",
        parse_mode=ParseMode.HTML,
    )


async def broadcast(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id not in settings.admin_ids:
        return
    text = " ".join(context.args).strip()
    if not text:
        await update.effective_message.reply_text("Use: /broadcast sua mensagem")
        return
    async with SessionLocal() as session:
        ids = list((await session.scalars(select(User.telegram_id).where(User.is_blocked.is_(False)))).all())
    sent = 0
    for chat_id in ids:
        try:
            await context.bot.send_message(chat_id=chat_id, text=text)
            sent += 1
        except Exception:
            pass
    await update.effective_message.reply_text(f"✅ Broadcast enviado para {sent}/{len(ids)} usuários.")


async def privacy_cmd(update: Update, context: CallbackContext) -> None:
    await update.effective_message.reply_text(
        "🔐 <b>Privacidade</b>\n\n"
        "O bot guarda seu ID do Telegram, os códigos que você cadastrou, apelidos e eventos de rastreio "
        "necessários para prestar o serviço. Tokens e chaves ficam apenas no servidor. "
        "CPF/CNPJ não é solicitado nem armazenado nesta versão.\n\n"
        "Use o botão 🗑 Remover em /meus para parar de acompanhar uma encomenda.",
        parse_mode=ParseMode.HTML,
    )


async def cancel_cmd(update: Update, context: CallbackContext) -> None:
    context.user_data.pop("rename_subscription_id", None)
    await update.effective_message.reply_text("✅ Operação cancelada.")
