from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.models import Shipment, Subscription, TrackingEvent
from app.utils import parse_datetime


MONTHS_PT = {
    1: "Jan.",
    2: "Fev.",
    3: "Mar.",
    4: "Abr.",
    5: "Mai.",
    6: "Jun.",
    7: "Jul.",
    8: "Ago.",
    9: "Set.",
    10: "Out.",
    11: "Nov.",
    12: "Dez.",
}

STATUS_HEADLINES = {
    "unknown": "Aguardando informações do seu pacote.",
    "info_received": "Seu pacote foi registrado.",
    "picked_up": "Seu pacote foi recebido pela transportadora.",
    "in_transit": "Seu pacote está em movimentação.",
    "customs": "Seu pacote está em fiscalização.",
    "arrived_destination": "Seu pacote chegou à região de destino.",
    "out_for_delivery": "Seu pacote saiu para entrega.",
    "available_for_pickup": "Seu pacote está disponível para retirada.",
    "delivery_failed": "A entrega não pôde ser concluída.",
    "exception": "Há uma ocorrência no transporte.",
    "returned": "Seu pacote está em devolução.",
    "delivered": "Seu pacote foi entregue.",
}

STATUS_DETAILS = {
    "unknown": "Ainda não há movimentações suficientes para detalhar o trajeto.",
    "info_received": "Os dados do envio foram recebidos e o pacote aguarda a próxima movimentação.",
    "picked_up": "A encomenda entrou na operação da transportadora e seguirá para o próximo centro logístico.",
    "in_transit": "O pacote está transitando entre a agência e o centro de distribuição mais próximo.",
    "customs": "A encomenda está em análise fiscal ou aduaneira antes de seguir viagem.",
    "arrived_destination": "O pacote chegou à região de destino e deve seguir para a etapa local de entrega.",
    "out_for_delivery": "O pacote está com a equipe responsável pela entrega ao destinatário.",
    "available_for_pickup": "O pacote está aguardando retirada no ponto ou unidade indicada pela transportadora.",
    "delivery_failed": "Houve uma tentativa de entrega sem conclusão. Uma nova tentativa ou retirada pode ser necessária.",
    "exception": "A transportadora registrou uma ocorrência que pode alterar o andamento normal da entrega.",
    "returned": "O pacote entrou no fluxo de devolução ao remetente.",
    "delivered": "A transportadora registrou a entrega como concluída.",
}


def _safe_extra(shipment: Shipment) -> dict:
    if not shipment.extra_json:
        return {}
    try:
        data = json.loads(shipment.extra_json)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _local_datetime(value: datetime, timezone_name: str) -> datetime:
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = timezone.utc
    return parse_datetime(value).astimezone(tz)


def _short_datetime(value: datetime, timezone_name: str) -> str:
    dt = _local_datetime(value, timezone_name)
    return f"{dt.day:02d} {MONTHS_PT[dt.month]} - {dt:%H:%M}"


def _format_delivery_date(value) -> str | None:
    if not value:
        return None
    text = str(value).strip()

    iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    if iso:
        return f"{iso.group(3)}/{iso.group(2)}/{iso.group(1)}"

    br = re.match(r"^(\d{2})/(\d{2})/(\d{4})", text)
    if br:
        return br.group(0)

    return text[:20] or None


def _clean_route(value) -> str | None:
    if not value:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _matching_raw_event(
    raw_events: list[dict],
    event_at: datetime | None,
) -> dict:
    if not raw_events:
        return {}

    if event_at is None:
        return max(
            raw_events,
            key=lambda item: parse_datetime(item.get("createdAt")),
        )

    target = parse_datetime(event_at).astimezone(timezone.utc)
    return min(
        raw_events,
        key=lambda item: abs(
            (
                parse_datetime(item.get("createdAt")).astimezone(timezone.utc)
                - target
            ).total_seconds()
        ),
    )


def extract_route_and_eta(
    shipment: Shipment,
    event_at: datetime | None = None,
) -> tuple[str | None, str | None, str | None]:
    raw = _safe_extra(shipment)
    raw_events = raw.get("trackingEvents") or []
    if isinstance(raw_events, dict):
        raw_events = [raw_events]
    raw_events = [
        item for item in raw_events
        if isinstance(item, dict)
    ]

    matched = _matching_raw_event(raw_events, event_at)
    origin = _clean_route(matched.get("from"))
    destination = _clean_route(matched.get("to"))
    eta = _format_delivery_date(raw.get("estimatedDelivery"))
    return origin, destination, eta


def format_tracking_card(
    subscription: Subscription,
    shipment: Shipment,
    timezone_name: str,
    *,
    event: TrackingEvent | None = None,
) -> str:
    current_status = event.status if event else shipment.status
    current_time = (
        event.event_at
        if event
        else shipment.last_event_at
    )
    current_location = (
        event.location
        if event
        else shipment.last_location
    )

    name = html.escape(
        subscription.nickname
        or shipment.carrier_name
        or "Minha encomenda"
    )
    number = html.escape(shipment.tracking_number)
    carrier = html.escape(
        shipment.carrier_name
        or "Transportadora em detecção"
    )

    title = STATUS_HEADLINES.get(
        current_status,
        STATUS_HEADLINES["unknown"],
    )
    detail = STATUS_DETAILS.get(
        current_status,
        STATUS_DETAILS["unknown"],
    )

    origin, destination, eta = extract_route_and_eta(
        shipment,
        current_time,
    )

    lines = [
        (
            "<blockquote>"
            f"🔎 <code>{number}</code>\n"
            f"<i>{name}</i>"
            "</blockquote>"
        ),
        "",
        f"📦 <b>Transportadora:</b> {carrier}",
        "",
    ]

    if current_time:
        lines.append(
            f"<b>{_short_datetime(current_time, timezone_name)} | "
            f"{html.escape(title)}</b>"
        )
    else:
        lines.append(
            f"<b>{html.escape(title)}</b>"
        )

    lines.extend(
        [
            "",
            html.escape(detail),
        ]
    )

    if origin and destination:
        lines.extend(
            [
                "",
                (
                    "📍 <b>"
                    f"{html.escape(origin)}"
                    " → "
                    f"{html.escape(destination)}"
                    "</b>"
                ),
            ]
        )
    elif current_location:
        lines.extend(
            [
                "",
                f"📍 <b>{html.escape(str(current_location))}</b>",
            ]
        )

    if eta:
        lines.extend(
            [
                "",
                (
                    "📅 <b>Previsão de entrega:</b> "
                    f"<i>{html.escape(eta)}</i>"
                ),
            ]
        )

    return "\n".join(lines)



def format_tracking_rich_html(
    subscription: Subscription,
    shipment: Shipment,
    timezone_name: str,
    *,
    event: TrackingEvent | None = None,
    warning: str | None = None,
) -> str:
    """Telegram Rich Message HTML using pull quote + bordered table."""
    current_status = event.status if event else shipment.status
    current_time = event.event_at if event else shipment.last_event_at
    current_location = event.location if event else shipment.last_location

    name = html.escape(
        subscription.nickname
        or shipment.carrier_name
        or "Minha encomenda"
    )
    number = html.escape(shipment.tracking_number)
    carrier = html.escape(
        shipment.carrier_name
        or "Transportadora em detecção"
    )

    title = STATUS_HEADLINES.get(
        current_status,
        STATUS_HEADLINES["unknown"],
    )
    detail = STATUS_DETAILS.get(
        current_status,
        STATUS_DETAILS["unknown"],
    )

    origin, destination, eta = extract_route_and_eta(
        shipment,
        current_time,
    )

    top = (
        "<aside>"
        f"🔎 <code>{number}</code><br>"
        f"<i>{name}</i>"
        "</aside>"
    )

    rows: list[str] = [
        (
            "<tr><td>"
            f"📦 <b>Transportadora:</b> {carrier}"
            "</td></tr>"
        )
    ]

    if current_time:
        headline = (
            f"{_short_datetime(current_time, timezone_name)} | "
            f"{html.escape(title)}"
        )
    else:
        headline = html.escape(title)

    rows.append(
        f"<tr><td><b>{headline}</b></td></tr>"
    )
    rows.append(
        f"<tr><td>{html.escape(detail)}</td></tr>"
    )

    if origin and destination:
        rows.append(
            "<tr><td>"
            f"📍 <b>{html.escape(origin)}</b>"
            " - Destino: "
            f"<b>{html.escape(destination)}</b>"
            "</td></tr>"
        )
    elif current_location:
        rows.append(
            "<tr><td>"
            f"📍 <b>{html.escape(str(current_location))}</b>"
            "</td></tr>"
        )

    if eta:
        rows.append(
            "<tr><td>"
            "📅 <b>Previsão de entrega:</b> "
            f"<i>{html.escape(eta)}</i>"
            "</td></tr>"
        )

    if warning:
        rows.append(
            "<tr><td>"
            "⚠️ "
            f"{html.escape(warning)}"
            "</td></tr>"
        )

    return top + "<table bordered>" + "".join(rows) + "</table>"
