from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.models import Shipment, Subscription, TrackingEvent
from app.utils import (
    parse_datetime,
    parse_datetime_assuming_timezone,
)

MONTHS_PT = {
    1: "Jan.", 2: "Fev.", 3: "Mar.", 4: "Abr.",
    5: "Mai.", 6: "Jun.", 7: "Jul.", 8: "Ago.",
    9: "Set.", 10: "Out.", 11: "Nov.", 12: "Dez.",
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


def _short_datetime(value: datetime, timezone_name: str) -> str:
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = timezone.utc
    dt = parse_datetime(value).astimezone(tz)
    return f"{dt.day:02d} {MONTHS_PT[dt.month]} - {dt:%H:%M}"


def _source_datetime(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = parse_datetime_assuming_timezone(
            value,
            "America/Sao_Paulo",
        )
        return dt.astimezone(ZoneInfo("America/Sao_Paulo"))
    except Exception:
        return None


def _short_source_datetime(value) -> str | None:
    dt = _source_datetime(value)
    if not dt:
        return None
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


def _correios_unit(unit) -> str | None:
    if not isinstance(unit, dict):
        return None
    address = unit.get("endereco") or {}
    if not isinstance(address, dict):
        address = {}
    city = address.get("cidade") or unit.get("cidade")
    state = address.get("uf") or unit.get("uf")
    if city and state:
        return f"{str(city).strip()}/{str(state).strip()}"
    if city:
        return str(city).strip()
    return _clean_route(unit.get("nome"))


def _normalized_raw_events(raw: dict) -> list[dict]:
    events = raw.get("trackingEvents") or []
    if isinstance(events, dict):
        events = [events]
    result = [dict(x) for x in events if isinstance(x, dict)]
    if result:
        return result

    objects = raw.get("objetos") or []
    if not objects or not isinstance(objects[0], dict):
        return []

    output = []
    for item in objects[0].get("eventos") or []:
        if not isinstance(item, dict):
            continue
        created = item.get("dtHrCriado")
        if isinstance(created, dict):
            created = created.get("date")
        output.append({
            "createdAt": created,
            "from": _correios_unit(item.get("unidade")),
            "to": _correios_unit(item.get("unidadeDestino")),
        })
    return output


def _matching_raw_event(raw_events: list[dict], event_at) -> dict:
    if not raw_events:
        return {}

    def dt(item):
        return (
            _source_datetime(item.get("createdAt"))
            or datetime.min.replace(tzinfo=timezone.utc)
        )

    if event_at is None:
        return max(raw_events, key=dt)

    target = parse_datetime(event_at).astimezone(timezone.utc)
    return min(
        raw_events,
        key=lambda item: abs(
            (dt(item).astimezone(timezone.utc) - target).total_seconds()
        ),
    )


def extract_tracking_metadata(
    shipment: Shipment,
    event_at=None,
) -> tuple[str | None, str | None, str | None, str | None]:
    raw = _safe_extra(shipment)
    matched = _matching_raw_event(
        _normalized_raw_events(raw),
        event_at,
    )

    origin = _clean_route(matched.get("from"))
    destination = _clean_route(matched.get("to"))

    eta_value = raw.get("estimatedDelivery")
    if not eta_value:
        objects = raw.get("objetos") or []
        if objects and isinstance(objects[0], dict):
            obj = objects[0]
            eta_value = (
                obj.get("dtPrevista")
                or obj.get("dataPrevista")
                or obj.get("previsaoEntrega")
            )

    return (
        origin,
        destination,
        _format_delivery_date(eta_value),
        _short_source_datetime(matched.get("createdAt")),
    )


def _card_context(subscription, shipment, timezone_name, event):
    status = event.status if event else shipment.status
    event_at = event.event_at if event else shipment.last_event_at
    location = event.location if event else shipment.last_location

    origin, destination, eta, source_time = extract_tracking_metadata(
        shipment,
        event_at,
    )

    display_time = source_time
    if not display_time and event_at:
        display_time = _short_datetime(event_at, timezone_name)

    return {
        "name": html.escape(
            subscription.nickname
            or shipment.carrier_name
            or "Minha encomenda"
        ),
        "number": html.escape(shipment.tracking_number),
        "carrier": html.escape(
            shipment.carrier_name
            or "Transportadora em detecção"
        ),
        "title": STATUS_HEADLINES.get(status, STATUS_HEADLINES["unknown"]),
        "detail": STATUS_DETAILS.get(status, STATUS_DETAILS["unknown"]),
        "location": location,
        "origin": origin,
        "destination": destination,
        "eta": eta,
        "display_time": display_time,
    }


def format_tracking_card(
    subscription: Subscription,
    shipment: Shipment,
    timezone_name: str,
    *,
    event: TrackingEvent | None = None,
) -> str:
    ctx = _card_context(subscription, shipment, timezone_name, event)
    lines = [
        (
            "<blockquote>"
            f"🔎 <code>{ctx['number']}</code>\n"
            f"<i>{ctx['name']}</i>"
            "</blockquote>"
        ),
        "",
        f"📦 <b>Transportadora:</b> {ctx['carrier']}",
        "",
    ]

    headline = html.escape(ctx["title"])
    if ctx["display_time"]:
        headline = f"{ctx['display_time']} | {headline}"
    lines.append(f"<b>{headline}</b>")
    lines.extend(["", html.escape(ctx["detail"])])

    if ctx["origin"] and ctx["destination"]:
        lines.extend([
            "",
            (
                "📍 <b>"
                f"{html.escape(ctx['origin'])}"
                " → "
                f"{html.escape(ctx['destination'])}"
                "</b>"
            ),
        ])
    elif ctx["location"]:
        lines.extend([
            "",
            f"📍 <b>{html.escape(str(ctx['location']))}</b>",
        ])

    if ctx["eta"]:
        lines.extend([
            "",
            (
                "📅 <b>Previsão de entrega:</b> "
                f"<i>{html.escape(ctx['eta'])}</i>"
            ),
        ])

    return "\n".join(lines)


def format_tracking_rich_html(
    subscription: Subscription,
    shipment: Shipment,
    timezone_name: str,
    *,
    event: TrackingEvent | None = None,
    warning: str | None = None,
) -> str:
    ctx = _card_context(subscription, shipment, timezone_name, event)

    top = (
        "<aside>"
        f"🔎 <code>{ctx['number']}</code><br>"
        f"<i>{ctx['name']}</i>"
        "</aside>"
    )

    rows = [
        (
            "<tr><td>"
            f"📦 <b>Transportadora:</b> {ctx['carrier']}"
            "</td></tr>"
        )
    ]

    headline = html.escape(ctx["title"])
    if ctx["display_time"]:
        headline = f"{ctx['display_time']} | {headline}"
    rows.append(f"<tr><td><b>{headline}</b></td></tr>")
    rows.append(f"<tr><td>{html.escape(ctx['detail'])}</td></tr>")

    if ctx["origin"] and ctx["destination"]:
        rows.append(
            "<tr><td>"
            "📍 "
            f"<b>{html.escape(ctx['origin'])}</b>"
            " → "
            f"<b>{html.escape(ctx['destination'])}</b>"
            "</td></tr>"
        )
    elif ctx["location"]:
        rows.append(
            "<tr><td>"
            "📍 "
            f"<b>{html.escape(str(ctx['location']))}</b>"
            "</td></tr>"
        )

    if ctx["eta"]:
        rows.append(
            "<tr><td>"
            "📅 <b>Previsão de entrega:</b> "
            f"<i>{html.escape(ctx['eta'])}</i>"
            "</td></tr>"
        )

    if warning:
        rows.append(
            "<tr><td>⚠️ "
            f"{html.escape(warning)}"
            "</td></tr>"
        )

    return top + "<table bordered>" + "".join(rows) + "</table>"
