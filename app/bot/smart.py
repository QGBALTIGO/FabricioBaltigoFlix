from __future__ import annotations

import asyncio
import html
import logging
import secrets
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext

from app.config import get_settings
from app.database import SessionLocal
from app.intake import (
    TrackingCandidate,
    barcode_runtime_available,
    barcode_tracking_candidates_from_image_bytes,
    extract_tracking_candidates,
    ocr_runtime_available,
    ocr_text_from_image_bytes,
)
from app.models import (
    Shipment,
    Subscription,
    User,
)
from app.services.insights import (
    estimate_delivery_window,
    format_delivery_estimate,
    package_bucket,
)
from app.services.preferences import (
    get_subscription_preference,
)
from app.services.telemetry import (
    record_runtime_event,
)
from app.status import status_label

log = logging.getLogger(__name__)
settings = get_settings()

_ocr_last_use: dict[int, float] = {}
_ocr_semaphore = asyncio.Semaphore(
    max(1, settings.image_ocr_concurrency)
)


def _queue_image_metric(
    update: Update,
    context: CallbackContext,
    *,
    name: str,
    ok: bool,
    started: float,
    detail: str | None = None,
) -> None:
    duration_ms = max(
        0,
        int(
            round(
                (
                    time.monotonic()
                    - started
                )
                * 1000
            )
        ),
    )
    context.application.create_task(
        record_runtime_event(
            kind="image_scan",
            name=name,
            ok=ok,
            duration_ms=duration_ms,
            detail=detail,
        ),
        update=update,
        name=(
            "image-scan-telemetry-"
            + name
        ),
    )


def _tracking_service(context: CallbackContext):
    return context.application.bot_data["tracking_service"]


async def _require_member(
    update: Update,
    context: CallbackContext,
) -> bool:
    # Import lazily so handlers can also import offer_smart_candidates.
    from app.bot import handlers

    return await handlers._require_channel_membership(
        update,
        context,
    )


async def _current_user(
    session,
    update: Update,
    context: CallbackContext,
) -> User:
    tg = update.effective_user
    user = await _tracking_service(context).ensure_user(
        session,
        tg.id,
        tg.username,
        tg.first_name,
    )
    await session.flush()
    return user


def _candidate_markup(
    candidates: list[TrackingCandidate],
    context: CallbackContext,
) -> InlineKeyboardMarkup:
    saved = context.user_data.setdefault(
        "smart_tracking_candidates",
        {},
    )

    # Keep a bounded ephemeral inbox per Telegram session.
    if len(saved) > 12:
        for key in list(saved)[:-8]:
            saved.pop(key, None)

    rows: list[list[InlineKeyboardButton]] = []
    for candidate in candidates[:5]:
        token = secrets.token_urlsafe(6)[:10]
        saved[token] = candidate.to_dict()
        label = candidate.nickname or candidate.number
        rows.append(
            [
                InlineKeyboardButton(
                    f"✅ {label[:42]}",
                    callback_data=f"smartadd:{token}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                "❌ Cancelar",
                callback_data="smartcancel:all",
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


def _candidate_preview_text(
    candidates: list[TrackingCandidate],
    *,
    image: bool = False,
) -> str:
    title = (
        "📦 <b>Encontrei um rastreio na imagem</b>"
        if image
        else "📨 <b>Encontrei um código de rastreio</b>"
    )

    lines = [
        title,
        "",
        "Confirme antes de eu começar a acompanhar:",
        "",
    ]

    for index, candidate in enumerate(candidates[:5], 1):
        lines.append(
            f"{index}. 🔎 <code>{html.escape(candidate.number)}</code>"
        )
        if candidate.store_name:
            lines.append(
                f"   🏪 {html.escape(candidate.store_name)}"
            )
        if candidate.product_name:
            lines.append(
                f"   🛍 {html.escape(candidate.product_name)}"
            )
        elif candidate.order_number:
            lines.append(
                f"   🧾 Pedido {html.escape(candidate.order_number)}"
            )

    lines.extend(
        [
            "",
            "<i>Nada é salvo até você confirmar.</i>",
        ]
    )
    return "\n".join(lines)


async def offer_smart_candidates(
    update: Update,
    context: CallbackContext,
    text: str,
    *,
    source: str = "message",
    image: bool = False,
) -> bool:
    candidates = extract_tracking_candidates(
        text,
        source=source,
    )
    if not candidates:
        return False

    await update.effective_message.reply_text(
        _candidate_preview_text(
            candidates,
            image=image,
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=_candidate_markup(
            candidates,
            context,
        ),
    )
    return True


async def overview_cmd(
    update: Update,
    context: CallbackContext,
) -> None:
    if not await _require_member(update, context):
        return

    text, markup = await _build_overview(
        update,
        context,
    )
    await update.effective_message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=markup,
    )


# Compatibilidade com comandos/botões antigos já enviados no Telegram.
today_cmd = overview_cmd


async def _load_user_subscriptions(
    session,
    update: Update,
    context: CallbackContext,
) -> tuple[User, list[Subscription]]:
    user = await _current_user(
        session,
        update,
        context,
    )
    subs = list(
        (
            await session.scalars(
                select(Subscription)
                .options(
                    selectinload(
                        Subscription.shipment
                    )
                )
                .where(
                    Subscription.user_id == user.id,
                    Subscription.is_active.is_(True),
                )
                .order_by(
                    Subscription.id.desc(),
                )
            )
        ).all()
    )
    return user, subs


def _overview_summary_lines(
    buckets: dict[str, list[Subscription]],
) -> list[str]:
    lines: list[str] = []

    counts = {
        key: len(value)
        for key, value in buckets.items()
    }

    if counts["out_for_delivery"]:
        count = counts["out_for_delivery"]
        verb = "saiu" if count == 1 else "saíram"
        lines.append(
            f"🛵 <b>{count} {verb} para entrega</b>"
        )

    if counts["near"]:
        count = counts["near"]
        verb = "chegou" if count == 1 else "chegaram"
        lines.append(
            f"📍 <b>{count} {verb} à região de destino</b>"
        )

    if counts["attention"]:
        count = counts["attention"]
        verb = "precisa" if count == 1 else "precisam"
        lines.append(
            f"⚠️ <b>{count} {verb} de atenção</b>"
        )

    if counts["transit"]:
        count = counts["transit"]
        lines.append(
            f"🚚 <b>{count} em trânsito</b>"
        )

    if counts["delivered_today"]:
        count = counts["delivered_today"]
        label = (
            "entregue hoje"
            if count == 1
            else "entregues hoje"
        )
        lines.append(
            f"✅ <b>{count} {label}</b>"
        )

    return lines


async def _build_overview(
    update: Update,
    context: CallbackContext,
) -> tuple[str, InlineKeyboardMarkup]:
    async with SessionLocal() as session:
        _, subs = await _load_user_subscriptions(
            session,
            update,
            context,
        )

        buckets: dict[str, list[Subscription]] = {
            "out_for_delivery": [],
            "near": [],
            "attention": [],
            "transit": [],
            "delivered_today": [],
        }

        for sub in subs:
            bucket = package_bucket(
                sub.shipment,
                stale_after_hours=settings.stale_after_hours,
                timezone_name=settings.display_timezone,
            )
            if bucket in buckets:
                buckets[bucket].append(sub)

        highlights = (
            buckets["out_for_delivery"]
            + buckets["attention"]
            + buckets["near"]
        )
        if not highlights:
            highlights = buckets["transit"]

        estimate_by_sub: dict[int, str] = {}
        for sub in highlights[:3]:
            estimate = await estimate_delivery_window(
                session,
                sub.shipment,
            )
            formatted = format_delivery_estimate(
                estimate,
                settings.display_timezone,
            )
            if formatted:
                estimate_by_sub[sub.id] = formatted

    if not subs:
        text = (
            "📦 <b>Visão geral</b>\n\n"
            "Nenhuma encomenda acompanhada por enquanto.\n"
            "Envie um código, uma mensagem da loja ou um print para começar."
        )
    else:
        text_lines = [
            "📦 <b>Visão geral</b>",
            "",
        ]

        summary = _overview_summary_lines(
            buckets
        )
        if summary:
            text_lines.extend(summary)
        else:
            text_lines.append(
                "Nenhuma movimentação em destaque no momento."
            )

        if highlights:
            text_lines.extend(
                [
                    "",
                    "<b>Em destaque</b>",
                ]
            )

            for sub in highlights[:3]:
                shipment = sub.shipment
                name = (
                    sub.nickname
                    or shipment.carrier_name
                    or shipment.tracking_number
                )
                status = status_label(
                    shipment.status
                )
                parts = status.split(
                    " ",
                    1,
                )
                emoji = parts[0]
                label = (
                    parts[1]
                    if len(parts) > 1
                    else status
                )

                text_lines.append(
                    f"{emoji} <b>{html.escape(name[:80])}</b> — "
                    f"{html.escape(label)}"
                )

                prediction = estimate_by_sub.get(
                    sub.id
                )
                if prediction:
                    text_lines.append(
                        "   <i>Previsão estimada: "
                        + html.escape(prediction)
                        + "</i>"
                    )

        text = "\n".join(text_lines)

    rows: list[list[InlineKeyboardButton]] = []
    button_specs = (
        ("out_for_delivery", "🛵", "Para entrega"),
        ("attention", "⚠️", "Atenção"),
        ("near", "📍", "Na região"),
        ("transit", "🚚", "Em trânsito"),
        ("delivered_today", "✅", "Entregues"),
    )

    for key, emoji, label in button_specs:
        count = len(
            buckets[key]
        )
        if count:
            rows.append(
                [
                    InlineKeyboardButton(
                        f"{emoji} {label} · {count}",
                        callback_data=(
                            f"overviewlist:{key}"
                        ),
                    )
                ]
            )

    rows.append(
        [
            InlineKeyboardButton(
                "🔄 Atualizar",
                callback_data="overview:refresh",
            )
        ]
    )

    return text, InlineKeyboardMarkup(rows)


def _overview_list_copy(
    bucket_name: str,
    count: int,
) -> tuple[str, str]:
    singular = count == 1

    if bucket_name == "out_for_delivery":
        return (
            "🛵 <b>Saiu para entrega</b>",
            (
                "1 encomenda está a caminho do endereço de entrega."
                if singular
                else f"{count} encomendas estão a caminho do endereço de entrega."
            ),
        )

    if bucket_name == "near":
        return (
            "📍 <b>Na região de destino</b>",
            (
                "1 encomenda já chegou à região de destino."
                if singular
                else f"{count} encomendas já chegaram à região de destino."
            ),
        )

    if bucket_name == "attention":
        return (
            "⚠️ <b>Atenção</b>",
            (
                "1 encomenda tem uma atualização que merece atenção."
                if singular
                else f"{count} encomendas têm atualizações que merecem atenção."
            ),
        )

    if bucket_name == "delivered_today":
        return (
            "✅ <b>Entregues hoje</b>",
            (
                "1 encomenda foi entregue hoje."
                if singular
                else f"{count} encomendas foram entregues hoje."
            ),
        )

    return (
        "🚚 <b>Em trânsito</b>",
        (
            "1 encomenda segue em movimentação."
            if singular
            else f"{count} encomendas seguem em movimentação."
        ),
    )


async def _overview_list(
    update: Update,
    context: CallbackContext,
    bucket_name: str,
) -> tuple[str, InlineKeyboardMarkup]:
    async with SessionLocal() as session:
        _, subs = await _load_user_subscriptions(
            session,
            update,
            context,
        )

    filtered = [
        sub
        for sub in subs
        if package_bucket(
            sub.shipment,
            stale_after_hours=settings.stale_after_hours,
            timezone_name=settings.display_timezone,
        )
        == bucket_name
    ]

    title, description = _overview_list_copy(
        bucket_name,
        len(filtered),
    )

    rows: list[list[InlineKeyboardButton]] = []
    for sub in filtered[:20]:
        shipment = sub.shipment
        name = (
            sub.nickname
            or shipment.carrier_name
            or shipment.tracking_number
        )
        emoji = status_label(
            shipment.status
        ).split(
            " ",
            1,
        )[0]

        rows.append(
            [
                InlineKeyboardButton(
                    f"{emoji} {name[:42]}",
                    callback_data=f"open:{sub.id}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                "⬅️ Visão geral",
                callback_data="overview:refresh",
            )
        ]
    )

    text = (
        f"{title}\n\n"
        f"{description}\n\n"
        "Selecione uma encomenda para abrir o rastreio."
    )

    return text, InlineKeyboardMarkup(rows)


def _alert_menu_markup(
    sub: Subscription,
    pref,
) -> InlineKeyboardMarkup:
    def mark(value: bool) -> str:
        return "✅" if value else "⬜"

    if pref and pref.custom_alerts_enabled:
        out = bool(pref.alert_out_for_delivery)
        delivered = bool(pref.alert_delivered)
        problems = bool(pref.alert_problems)
        intermediate = bool(pref.alert_intermediate)
    else:
        out = delivered = problems = intermediate = True

    master = (
        "🔕 Desativar todos"
        if sub.notifications_enabled
        else "🔔 Ativar alertas"
    )

    rows = [
        [
            InlineKeyboardButton(
                master,
                callback_data=f"alerttoggle:{sub.id}:master",
            )
        ],
        [
            InlineKeyboardButton(
                f"{mark(intermediate)} Movimentações",
                callback_data=f"alerttoggle:{sub.id}:intermediate",
            )
        ],
        [
            InlineKeyboardButton(
                f"{mark(out)} Saiu para entrega",
                callback_data=f"alerttoggle:{sub.id}:out",
            )
        ],
        [
            InlineKeyboardButton(
                f"{mark(problems)} Problemas/retirada",
                callback_data=f"alerttoggle:{sub.id}:problems",
            )
        ],
        [
            InlineKeyboardButton(
                f"{mark(delivered)} Entregue",
                callback_data=f"alerttoggle:{sub.id}:delivered",
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Voltar ao rastreio",
                callback_data=f"open:{sub.id}",
            )
        ],
    ]
    return InlineKeyboardMarkup(rows)


async def _alert_menu(
    session,
    sub: Subscription,
) -> tuple[str, InlineKeyboardMarkup]:
    pref = await get_subscription_preference(
        session,
        sub.id,
        create=True,
    )
    await session.commit()

    name = (
        sub.nickname
        or sub.shipment.carrier_name
        or sub.shipment.tracking_number
    )
    mode = (
        "Personalizado"
        if pref and pref.custom_alerts_enabled
        else "Todas as movimentações (padrão)"
    )
    status = (
        "Ativados"
        if sub.notifications_enabled
        else "Desativados"
    )

    text = (
        "🔔 <b>Alertas</b>\n\n"
        f"📦 <b>{html.escape(name)}</b>\n"
        f"Estado: <b>{status}</b>\n"
        f"Modo: <b>{mode}</b>\n\n"
        "Use o botão principal para ligar ou desligar todos os alertas. "
        "Abaixo, você também pode escolher exatamente quais tipos de atualização quer receber."
    )
    return text, _alert_menu_markup(
        sub,
        pref,
    )


async def photo_tracking(
    update: Update,
    context: CallbackContext,
) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    if not await _require_member(update, context):
        return

    caption = str(
        message.caption
        or ""
    ).strip()
    if caption:
        candidates = extract_tracking_candidates(
            caption,
            source="image_caption",
        )
        if candidates:
            await message.reply_text(
                _candidate_preview_text(
                    candidates,
                    image=True,
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=_candidate_markup(
                    candidates,
                    context,
                ),
            )
            return

    barcode_available = (
        settings.image_barcode_scan_enabled
        and barcode_runtime_available()
    )
    ocr_available = (
        settings.image_ocr_enabled
        and ocr_runtime_available()
    )

    if (
        not barcode_available
        and not ocr_available
    ):
        await message.reply_text(
            "📸 A leitura de etiquetas está temporariamente indisponível. "
            "Envie o código de rastreio como texto."
        )
        return

    now = time.monotonic()
    last = _ocr_last_use.get(
        user.id,
        0.0,
    )
    cooldown = max(
        0.0,
        settings.image_ocr_cooldown_seconds,
    )
    if now - last < cooldown:
        await message.reply_text(
            "📸 Aguarde alguns segundos antes de enviar outra imagem."
        )
        return
    _ocr_last_use[user.id] = now

    media = None
    suffix = ".jpg"
    if message.photo:
        media = message.photo[-1]
    elif message.document:
        media = message.document
        filename = str(
            message.document.file_name
            or ""
        )
        suffix = (
            Path(filename).suffix.lower()
            or ".jpg"
        )

    if media is None:
        return

    file_size = int(
        getattr(
            media,
            "file_size",
            0,
        )
        or 0
    )
    if (
        file_size
        and file_size
        > settings.image_ocr_max_bytes
    ):
        await message.reply_text(
            "📸 Essa imagem é grande demais para leitura. "
            "Envie uma imagem de até "
            f"{settings.image_ocr_max_bytes // (1024 * 1024)} MB."
        )
        return

    working = await message.reply_text(
        "📸 Analisando a etiqueta…"
    )

    try:
        tg_file = await context.bot.get_file(
            media.file_id
        )
        raw = await tg_file.download_as_bytearray()
        raw_bytes = bytes(raw)

        if (
            len(raw_bytes)
            > settings.image_ocr_max_bytes
        ):
            await working.edit_text(
                "📸 Essa imagem é grande demais para leitura."
            )
            return

        barcode_candidates: list[
            TrackingCandidate
        ] = []

        if barcode_available:
            barcode_started = time.monotonic()
            try:
                async with _ocr_semaphore:
                    barcode_candidates = (
                        await asyncio.wait_for(
                            asyncio.to_thread(
                                barcode_tracking_candidates_from_image_bytes,
                                raw_bytes,
                            ),
                            timeout=max(
                                2.0,
                                settings.image_barcode_timeout_seconds,
                            ),
                        )
                    )

                _queue_image_metric(
                    update,
                    context,
                    name="barcode",
                    ok=bool(
                        barcode_candidates
                    ),
                    started=barcode_started,
                    detail=(
                        None
                        if barcode_candidates
                        else "no_candidate"
                    ),
                )
            except asyncio.TimeoutError:
                _queue_image_metric(
                    update,
                    context,
                    name="barcode",
                    ok=False,
                    started=barcode_started,
                    detail="timeout",
                )
                log.warning(
                    "Scanner de barcode excedeu o tempo; usando OCR."
                )
            except Exception:
                _queue_image_metric(
                    update,
                    context,
                    name="barcode",
                    ok=False,
                    started=barcode_started,
                    detail="error",
                )
                log.exception(
                    "Falha no scanner de barcode; usando OCR."
                )

        if barcode_candidates:
            await working.edit_text(
                _candidate_preview_text(
                    barcode_candidates,
                    image=True,
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=_candidate_markup(
                    barcode_candidates,
                    context,
                ),
            )
            return

        if not ocr_available:
            await working.edit_text(
                "🔎 Não encontrei um código de rastreio legível na etiqueta. "
                "Tente aproximar a câmera do código ou envie o código como texto."
            )
            return

        ocr_started = time.monotonic()
        async with _ocr_semaphore:
            ocr_text = await asyncio.wait_for(
                asyncio.to_thread(
                    ocr_text_from_image_bytes,
                    raw_bytes,
                    suffix=suffix,
                ),
                timeout=max(
                    5.0,
                    settings.image_ocr_timeout_seconds,
                ),
            )

        candidates = extract_tracking_candidates(
            ocr_text,
            source="image_ocr",
        )
        _queue_image_metric(
            update,
            context,
            name="ocr",
            ok=bool(
                candidates
            ),
            started=ocr_started,
            detail=(
                None
                if candidates
                else "no_candidate"
            ),
        )
        if not candidates:
            await working.edit_text(
                "🔎 Analisei a etiqueta, mas não encontrei um código de rastreio com segurança. "
                "Tente uma foto mais próxima da etiqueta ou envie o código como texto."
            )
            return

        await working.edit_text(
            _candidate_preview_text(
                candidates,
                image=True,
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=_candidate_markup(
                candidates,
                context,
            ),
        )

    except asyncio.TimeoutError:
        if "ocr_started" in locals():
            _queue_image_metric(
                update,
                context,
                name="ocr",
                ok=False,
                started=ocr_started,
                detail="timeout",
            )
        await working.edit_text(
            "⏱ A leitura demorou demais. "
            "Tente uma foto mais próxima da etiqueta."
        )
    except Exception:
        if "ocr_started" in locals():
            _queue_image_metric(
                update,
                context,
                name="ocr",
                ok=False,
                started=ocr_started,
                detail="error",
            )
        log.exception(
            "Falha ao analisar imagem de rastreio"
        )
        await working.edit_text(
            "📸 Não consegui analisar essa imagem agora. "
            "Você pode enviar o código como texto."
        )


async def _owned_subscription(
    session,
    update: Update,
    context: CallbackContext,
    sub_id: int,
) -> tuple[User | None, Subscription | None]:
    user = await session.scalar(
        select(User).where(
            User.telegram_id == update.effective_user.id
        )
    )
    if not user:
        return None, None

    sub = await session.scalar(
        select(Subscription)
        .options(
            selectinload(
                Subscription.shipment
            )
        )
        .where(
            Subscription.id == int(sub_id),
            Subscription.user_id == user.id,
        )
    )
    return user, sub


async def smart_callback(
    update: Update,
    context: CallbackContext,
) -> None:
    query = update.callback_query
    if not query:
        return

    data = query.data or ""

    if data in {"overview:refresh", "today:refresh"}:
        await query.answer()
        text, markup = await _build_overview(
            update,
            context,
        )
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )
        return

    if (
        data.startswith("overviewlist:")
        or data.startswith("todaylist:")
    ):
        await query.answer()
        bucket_name = data.split(":", 1)[1]
        text, markup = await _overview_list(
            update,
            context,
            bucket_name,
        )
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )
        return

    if data.startswith("smartcancel:"):
        await query.answer()
        context.user_data.pop(
            "smart_tracking_candidates",
            None,
        )
        await query.edit_message_text(
            "❌ Cadastro cancelado. Nada foi salvo."
        )
        return

    if data.startswith("smartadd:"):
        await query.answer()
        token = data.split(":", 1)[1]
        saved = context.user_data.get(
            "smart_tracking_candidates",
            {},
        )
        candidate = saved.pop(token, None)
        if not candidate:
            await query.edit_message_text(
                "⌛ Essa confirmação expirou. Envie a mensagem ou o print novamente."
            )
            return

        await query.edit_message_text(
            "✅ Código confirmado. Vou consultar e começar a acompanhar."
        )

        from app.bot import handlers

        await handlers.add_tracking(
            update,
            context,
            candidate["number"],
            nickname=candidate.get("nickname"),
            intake_metadata=candidate,
        )
        return

    parts = data.split(":")
    if len(parts) < 2 or not parts[1].isdigit():
        return
    sub_id = int(parts[1])

    async with SessionLocal() as session:
        user, sub = await _owned_subscription(
            session,
            update,
            context,
            sub_id,
        )
        if not user or not sub:
            await query.answer(
                "Rastreio não encontrado.",
                show_alert=True,
            )
            return

        await query.answer()

        if data.startswith("alertmenu:"):
            text, markup = await _alert_menu(
                session,
                sub,
            )
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )
            return

        if data.startswith("alerttoggle:"):
            field = parts[2] if len(parts) > 2 else ""
            pref = await get_subscription_preference(
                session,
                sub.id,
                create=True,
            )
            assert pref is not None

            if field == "master":
                sub.notifications_enabled = (
                    not sub.notifications_enabled
                )
                if sub.notifications_enabled:
                    sub.notify_level = "all"
            else:
                pref.custom_alerts_enabled = True
                attr = {
                    "intermediate": "alert_intermediate",
                    "out": "alert_out_for_delivery",
                    "problems": "alert_problems",
                    "delivered": "alert_delivered",
                }.get(field)
                if attr:
                    setattr(
                        pref,
                        attr,
                        not bool(getattr(pref, attr)),
                    )

            await session.commit()
            text, markup = await _alert_menu(
                session,
                sub,
            )
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )
            return
