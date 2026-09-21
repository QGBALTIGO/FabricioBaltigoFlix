from __future__ import annotations

import html
from datetime import datetime, timezone

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import CallbackContext

from app.config import get_settings
from app.database import SessionLocal
from app.services.telemetry import (
    AdminHealthSnapshot,
    ProviderMetric,
    build_admin_health_snapshot,
    percentage,
    provider_label,
)

settings = get_settings()


def _latency_label(
    metric: ProviderMetric,
) -> str:
    if metric.avg_ms is None:
        return "—"
    if metric.avg_ms < 1000:
        return f"{metric.avg_ms} ms"
    return f"{metric.avg_ms / 1000:.1f} s"


def _remaining_label(
    until: datetime,
    *,
    now: datetime,
) -> str:
    end = (
        until.replace(
            tzinfo=timezone.utc
        )
        if until.tzinfo is None
        else until.astimezone(
            timezone.utc
        )
    )
    current = (
        now.replace(
            tzinfo=timezone.utc
        )
        if now.tzinfo is None
        else now.astimezone(
            timezone.utc
        )
    )
    seconds = max(
        0,
        int(
            (
                end
                - current
            ).total_seconds()
        ),
    )

    if seconds < 60:
        return "menos de 1 min"

    minutes = (
        seconds + 59
    ) // 60

    if minutes < 60:
        return (
            f"{minutes} min"
        )

    hours = minutes // 60
    remainder = minutes % 60

    if hours < 24:
        if remainder:
            return (
                f"{hours} h {remainder} min"
            )
        return f"{hours} h"

    days = hours // 24
    return (
        f"{days} dia"
        + (
            "s"
            if days != 1
            else ""
        )
    )


def _metric_line(
    metric: ProviderMetric,
    *,
    label: str | None = None,
    include_failures: bool = True,
) -> str:
    name = html.escape(
        label
        or metric.name
    )
    parts = [
        (
            f"{metric.queries} "
            + (
                "consulta"
                if metric.queries == 1
                else "consultas"
            )
        ),
        _latency_label(
            metric
        ),
    ]

    if include_failures:
        failures = metric.failures
        parts.append(
            (
                f"{failures} falha"
                if failures == 1
                else f"{failures} falhas"
            )
        )

    return (
        "• <b>"
        + name
        + "</b> · "
        + " · ".join(
            parts
        )
    )


def render_admin_health(
    snapshot: AdminHealthSnapshot,
) -> str:
    lines = [
        "🩺 <b>Saúde do Melhor Rastreio</b>",
        "<i>Fontes: última 1h · qualidade: últimas 24h</i>",
        "",
        "📦 <b>Operação</b>",
        f"• Pacotes ativos: <b>{snapshot.active_shipments}</b>",
        (
            "• Acompanhamentos ativos: "
            f"<b>{snapshot.active_subscriptions}</b>"
        ),
        (
            "• Aguardando consulta: "
            f"<b>{snapshot.poll_backlog}</b>"
        ),
        (
            "• Consultas às fontes: "
            f"<b>{snapshot.provider_queries_hour}/h</b>"
        ),
        "",
        "📡 <b>Fontes · 1h</b>",
    ]

    if snapshot.provider_metrics:
        for metric in (
            snapshot.provider_metrics[:8]
        ):
            lines.append(
                _metric_line(
                    metric,
                    label=provider_label(
                        metric.name
                    ),
                )
            )
    else:
        lines.append(
            "• Aguardando tráfego para calcular métricas."
        )

    if snapshot.fastest_provider:
        fastest = (
            snapshot.fastest_provider
        )
        lines.extend(
            [
                "",
                (
                    "⚡ <b>Mais rápida:</b> "
                    + html.escape(
                        provider_label(
                            fastest.name
                        )
                    )
                    + " · "
                    + _latency_label(
                        fastest
                    )
                ),
            ]
        )

    lines.extend(
        [
            "",
            "🚚 <b>Transportadoras · 1h</b>",
        ]
    )

    if snapshot.carrier_metrics:
        for metric in (
            snapshot.carrier_metrics[:6]
        ):
            lines.append(
                _metric_line(
                    metric,
                    include_failures=False,
                )
            )
    else:
        lines.append(
            "• Ainda sem amostra suficiente."
        )

    lines.extend(
        [
            "",
            "🧯 <b>Quarentena de fontes</b>",
        ]
    )

    if snapshot.quarantined:
        for item in snapshot.quarantined:
            failures = (
                f"{item.failures} falha"
                if item.failures == 1
                else f"{item.failures} falhas"
            )
            lines.append(
                "• "
                + html.escape(
                    provider_label(
                        item.name
                    )
                )
                + " · "
                + failures
                + " · mais "
                + _remaining_label(
                    item.until,
                    now=(
                        snapshot.generated_at
                    ),
                )
            )
    else:
        lines.append(
            "✅ Nenhuma fonte em quarentena."
        )

    notification_total = (
        snapshot.notification_sent_24h
        + snapshot.notification_failures_24h
    )
    lines.extend(
        [
            "",
            "🔔 <b>Notificações · 24h</b>",
            (
                "• Enviadas: "
                f"<b>{snapshot.notification_sent_24h}</b>"
            ),
            (
                "• Falhas: "
                f"<b>{snapshot.notification_failures_24h}</b>"
            ),
            (
                "• Sucesso: "
                f"<b>{percentage(snapshot.notification_sent_24h, notification_total)}</b>"
            ),
            "",
            "📸 <b>Scanner · 24h</b>",
            (
                "• QR/código de barras: "
                f"<b>{percentage(snapshot.barcode_successes_24h, snapshot.barcode_attempts_24h)}</b> "
                f"({snapshot.barcode_successes_24h}/{snapshot.barcode_attempts_24h})"
            ),
            (
                "• OCR: "
                f"<b>{percentage(snapshot.ocr_successes_24h, snapshot.ocr_attempts_24h)}</b> "
                f"({snapshot.ocr_successes_24h}/{snapshot.ocr_attempts_24h})"
            ),
            "",
            (
                "⚠️ <b>Erros técnicos · 24h:</b> "
                f"{snapshot.technical_errors_24h}"
            ),
            (
                "👥 Usuários: "
                f"{snapshot.users} · "
                "📚 Pacotes registrados: "
                f"{snapshot.shipments}"
            ),
        ]
    )

    return "\n".join(
        lines
    )


def admin_health_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔄 Atualizar",
                    callback_data="adminhealth:refresh",
                )
            ]
        ]
    )


async def _snapshot() -> AdminHealthSnapshot:
    async with SessionLocal() as session:
        return await build_admin_health_snapshot(
            session
        )


async def admin_health(
    update: Update,
    context: CallbackContext,
) -> None:
    user = update.effective_user
    if (
        not user
        or user.id
        not in settings.admin_ids
    ):
        return

    snapshot = await _snapshot()
    await update.effective_message.reply_text(
        render_admin_health(
            snapshot
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=admin_health_keyboard(),
    )


async def admin_health_callback(
    update: Update,
    context: CallbackContext,
) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query:
        return

    if (
        not user
        or user.id
        not in settings.admin_ids
    ):
        await query.answer(
            "Sem acesso.",
            show_alert=True,
        )
        return

    await query.answer(
        "Atualizado"
    )
    snapshot = await _snapshot()
    await query.edit_message_text(
        render_admin_health(
            snapshot
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=admin_health_keyboard(),
    )
