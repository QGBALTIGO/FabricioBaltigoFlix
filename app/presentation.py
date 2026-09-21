from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.models import Shipment, Subscription, TrackingEvent
from app.services.insights import action_advice
from app.status import status_label
from app.utils import (
    humanize_age,
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

    tracking = raw.get("tracking") or []
    if isinstance(tracking, dict):
        tracking = [tracking]

    if tracking and isinstance(tracking[0], dict):
        output = []

        def normalize_route_piece(
            value: str,
        ) -> str:
            value = re.sub(
                r"\s*/\s*",
                "/",
                value,
            )
            value = re.sub(
                r"\s+em\s+",
                " - ",
                value,
                count=1,
                flags=re.IGNORECASE,
            )
            return re.sub(
                r"\s+",
                " ",
                value,
            ).strip()

        for item in (
            tracking[0].get("Posicoes")
            or []
        ):
            if not isinstance(item, dict):
                continue

            details_formatted = str(
                item.get("DetalhesFormatado")
                or ""
            ).replace("\r", " ").replace(
                "\n",
                " ",
            )
            details_formatted = re.sub(
                r"\s+",
                " ",
                details_formatted,
            ).strip()

            origin = None
            destination = None
            route = re.search(
                r"\bSaiu de (.+?) para (.+)$",
                details_formatted,
                flags=re.IGNORECASE,
            )
            if route:
                origin = normalize_route_piece(
                    route.group(1)
                )
                destination = normalize_route_piece(
                    route.group(2)
                )

            output.append(
                {
                    "createdAt": item.get("Data"),
                    "description": (
                        item.get("Acao")
                        or "Atualização"
                    ),
                    "from": origin,
                    "to": destination,
                    "additionalInfo": (
                        item.get("Detalhes")
                        or None
                    ),
                }
            )

        if output:
            return output

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


def _card_context(
    subscription,
    shipment,
    timezone_name,
    event,
):
    status = (
        event.status
        if event
        else shipment.status
    )
    event_at = (
        event.event_at
        if event
        else shipment.last_event_at
    )
    location = (
        event.location
        if event
        else shipment.last_location
    )

    origin, destination, eta, source_time = (
        extract_tracking_metadata(
            shipment,
            event_at,
        )
    )

    display_time = source_time
    if not display_time and event_at:
        display_time = _short_datetime(
            event_at,
            timezone_name,
        )

    description = None
    if event:
        description = getattr(
            event,
            "description",
            None,
        )
    if not description:
        description = getattr(
            shipment,
            "last_description",
            None,
        )
    description = (
        _clean_route(
            description
        )
        or STATUS_DETAILS.get(
            status,
            STATUS_DETAILS["unknown"],
        )
    )

    updated_age = (
        humanize_age(
            event_at
        )
        if event_at
        else None
    )

    return {
        "name": html.escape(
            subscription.nickname
            or "Minha encomenda"
        ),
        "number": html.escape(
            shipment.tracking_number
        ),
        "carrier": html.escape(
            shipment.carrier_name
            or "Transportadora em detecção"
        ),
        "status": html.escape(
            status_label(
                status
            )
        ),
        "status_code": status,
        "detail": description,
        "location": location,
        "origin": origin,
        "destination": destination,
        "eta": eta,
        "display_time": display_time,
        "updated_age": updated_age,
    }


def format_tracking_card(
    subscription: Subscription,
    shipment: Shipment,
    timezone_name: str,
    *,
    event: TrackingEvent | None = None,
) -> str:
    ctx = _card_context(
        subscription,
        shipment,
        timezone_name,
        event,
    )

    lines = [
        (
            "<blockquote>"
            f"<b>📦 {ctx['name']}</b>\n"
            f"<code>{ctx['number']}</code>\n"
            f"🚚 <i>{ctx['carrier']}</i>"
            "</blockquote>"
        ),
        "",
        f"<b>{ctx['status']}</b>",
    ]

    update_parts: list[str] = []
    if ctx["display_time"]:
        update_parts.append(
            f"<i>{html.escape(ctx['display_time'])}</i>"
        )
    if ctx["detail"]:
        update_parts.append(
            html.escape(
                ctx["detail"]
            )
        )
    if update_parts:
        lines.extend(
            [
                "",
                "🕐 <b>Última atualização</b>",
                *update_parts,
            ]
        )

    if (
        ctx["origin"]
        and ctx["destination"]
    ):
        lines.extend(
            [
                "",
                (
                    "📍 <b>Rota atual</b>\n"
                    f"{html.escape(ctx['origin'])}"
                    " → "
                    f"{html.escape(ctx['destination'])}"
                ),
            ]
        )
    elif ctx["location"]:
        lines.extend(
            [
                "",
                "📍 <b>Local atual</b>\n"
                + html.escape(
                    str(
                        ctx["location"]
                    )
                ),
            ]
        )

    if (
        ctx["eta"]
        and ctx["status_code"]
        != "delivered"
    ):
        lines.extend(
            [
                "",
                (
                    "📅 <b>Previsão de entrega</b>\n"
                    f"{html.escape(ctx['eta'])}"
                ),
            ]
        )

    if ctx["updated_age"]:
        lines.extend(
            [
                "",
                (
                    "🕒 <i>Atualizado "
                    + html.escape(
                        ctx["updated_age"]
                    )
                    + "</i>"
                ),
            ]
        )

    return "\n".join(
        lines
    )


def format_tracking_rich_html(
    subscription: Subscription,
    shipment: Shipment,
    timezone_name: str,
    *,
    event: TrackingEvent | None = None,
    warning: str | None = None,
) -> str:
    ctx = _card_context(
        subscription,
        shipment,
        timezone_name,
        event,
    )

    top = (
        "<aside>"
        f"<b>📦 {ctx['name']}</b><br>"
        f"<code>{ctx['number']}</code><br>"
        f"🚚 <i>{ctx['carrier']}</i>"
        "</aside>"
    )

    rows = [
        (
            "<tr><td>"
            f"<b>{ctx['status']}</b>"
            "</td></tr>"
        )
    ]

    update_bits: list[str] = [
        "🕐 <b>Última atualização</b>"
    ]
    if ctx["display_time"]:
        update_bits.append(
            "<i>"
            + html.escape(
                ctx["display_time"]
            )
            + "</i>"
        )
    if ctx["detail"]:
        update_bits.append(
            html.escape(
                ctx["detail"]
            )
        )
    rows.append(
        "<tr><td>"
        + "<br>".join(
            update_bits
        )
        + "</td></tr>"
    )

    if (
        ctx["origin"]
        and ctx["destination"]
    ):
        rows.append(
            "<tr><td>"
            "📍 <b>Rota atual</b><br>"
            f"{html.escape(ctx['origin'])}"
            " → "
            f"{html.escape(ctx['destination'])}"
            "</td></tr>"
        )
    elif ctx["location"]:
        rows.append(
            "<tr><td>"
            "📍 <b>Local atual</b><br>"
            + html.escape(
                str(
                    ctx["location"]
                )
            )
            + "</td></tr>"
        )

    if (
        ctx["eta"]
        and ctx["status_code"]
        != "delivered"
    ):
        rows.append(
            "<tr><td>"
            "📅 <b>Previsão de entrega</b><br>"
            f"{html.escape(ctx['eta'])}"
            "</td></tr>"
        )

    if ctx["updated_age"]:
        rows.append(
            "<tr><td>"
            "🕒 <i>Atualizado "
            + html.escape(
                ctx["updated_age"]
            )
            + "</i>"
            "</td></tr>"
        )

    if warning:
        rows.append(
            "<tr><td>⚠️ "
            f"{html.escape(warning)}"
            "</td></tr>"
        )

    return (
        top
        + "<table bordered>"
        + "".join(rows)
        + "</table>"
    )



def format_tracking_notification_rich_html(
    subscription: Subscription,
    shipment: Shipment,
    event: TrackingEvent,
    timezone_name: str,
    *,
    new_events_count: int = 1,
) -> str:
    raw = _safe_extra(shipment)
    matched = _matching_raw_event(
        _normalized_raw_events(raw),
        event.event_at,
    )

    origin = _clean_route(matched.get("from"))
    destination = _clean_route(matched.get("to"))
    eta = _format_delivery_date(
        raw.get("estimatedDelivery")
    )

    source_time = _short_source_datetime(
        matched.get("createdAt")
    )
    display_time = (
        source_time
        or _short_datetime(
            event.event_at,
            timezone_name,
        )
    )

    description = (
        _clean_route(matched.get("description"))
        or _clean_route(event.description)
        or STATUS_DETAILS.get(
            event.status,
            "Movimentação registrada.",
        )
    )

    name = html.escape(
        subscription.nickname
        or shipment.carrier_name
        or "Minha encomenda"
    )
    number = html.escape(
        shipment.tracking_number
    )

    top = (
        "<aside>"
        "🔔 <b>Nova atualização</b><br>"
        f"🔎 <code>{number}</code><br>"
        f"<i>{name}</i>"
        "</aside>"
    )

    action_title, _ = action_advice(
        event.status
    )
    rows = [
        (
            "<tr><td>"
            f"<b>{html.escape(status_label(event.status))}</b><br>"
            f"<i>{html.escape(display_time)}</i>"
            "</td></tr>"
        ),
        (
            "<tr><td>"
            f"{html.escape(description)}"
            "</td></tr>"
        ),
        (
            "<tr><td>💡 <b>Agora:</b> "
            f"{html.escape(action_title)}"
            "</td></tr>"
        ),
    ]

    if new_events_count > 1:
        rows.append(
            "<tr><td>"
            "🧾 "
            f"<b>{new_events_count} novas movimentações</b> "
            "foram registradas desde a última consulta."
            "</td></tr>"
        )

    if origin and destination:
        rows.append(
            "<tr><td>"
            "📍 "
            f"<b>{html.escape(origin)}</b>"
            " → "
            f"<b>{html.escape(destination)}</b>"
            "</td></tr>"
        )
    elif event.location:
        rows.append(
            "<tr><td>"
            "📍 "
            f"<b>{html.escape(str(event.location))}</b>"
            "</td></tr>"
        )

    if (
        eta
        and event.status != "delivered"
    ):
        rows.append(
            "<tr><td>"
            "📅 <b>Previsão de entrega:</b> "
            f"<i>{html.escape(eta)}</i>"
            "</td></tr>"
        )

    if event.status == "delivered":
        rows.append(
            "<tr><td>"
            "🎉 <b>Entrega concluída com sucesso.</b>"
            "</td></tr>"
        )

    return (
        top
        + "<table bordered>"
        + "".join(rows)
        + "</table>"
    )


def format_tracking_notification_fallback(
    subscription: Subscription,
    shipment: Shipment,
    event: TrackingEvent,
    timezone_name: str,
    *,
    new_events_count: int = 1,
) -> str:
    raw = _safe_extra(shipment)
    matched = _matching_raw_event(
        _normalized_raw_events(raw),
        event.event_at,
    )

    origin = _clean_route(matched.get("from"))
    destination = _clean_route(matched.get("to"))
    eta = _format_delivery_date(
        raw.get("estimatedDelivery")
    )
    source_time = _short_source_datetime(
        matched.get("createdAt")
    )
    display_time = (
        source_time
        or _short_datetime(
            event.event_at,
            timezone_name,
        )
    )
    description = (
        _clean_route(matched.get("description"))
        or _clean_route(event.description)
        or "Movimentação registrada."
    )

    action_title, _ = action_advice(
        event.status
    )
    lines = [
        "🔔 <b>Nova atualização</b>",
        f"🔎 <code>{html.escape(shipment.tracking_number)}</code>",
        f"<i>{html.escape(subscription.nickname or shipment.carrier_name or 'Minha encomenda')}</i>",
        "",
        f"<b>{html.escape(status_label(event.status))}</b>",
        f"<i>{html.escape(display_time)}</i>",
        "",
        html.escape(description),
        "",
        "💡 <b>Agora:</b> "
        + html.escape(action_title),
    ]

    if new_events_count > 1:
        lines.extend([
            "",
            (
                "🧾 "
                f"<b>{new_events_count} novas movimentações</b> "
                "foram registradas desde a última consulta."
            ),
        ])

    if origin and destination:
        lines.extend([
            "",
            (
                "📍 <b>"
                + html.escape(origin)
                + " → "
                + html.escape(destination)
                + "</b>"
            ),
        ])
    elif event.location:
        lines.extend([
            "",
            f"📍 <b>{html.escape(str(event.location))}</b>",
        ])

    if (
        eta
        and event.status != "delivered"
    ):
        lines.extend([
            "",
            (
                "📅 <b>Previsão de entrega:</b> "
                f"<i>{html.escape(eta)}</i>"
            ),
        ])

    if event.status == "delivered":
        lines.extend([
            "",
            "🎉 <b>Entrega concluída com sucesso.</b>",
        ])

    return "\n".join(lines)


def _compact_value(value) -> str | None:
    if value is None:
        return None

    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            compact = _compact_value(item)
            if compact:
                label = re.sub(
                    r"([a-z])([A-Z])",
                    r"\1 \2",
                    str(key),
                ).replace("_", " ").strip()
                parts.append(
                    f"{label}: {compact}"
                )
        return " • ".join(parts) or None

    if isinstance(value, (list, tuple, set)):
        parts = [
            compact
            for item in value
            if (compact := _compact_value(item))
        ]
        return " • ".join(parts) or None

    text = re.sub(
        r"\s+",
        " ",
        str(value),
    ).strip()
    return text or None


def _detailed_location(raw_event: dict) -> str | None:
    location = raw_event.get("location") or {}
    if not isinstance(location, dict):
        return _compact_value(location)

    address_line = []
    address = _clean_route(location.get("address"))
    number = _clean_route(location.get("number"))
    complement = _clean_route(location.get("complement"))

    if address:
        address_line.append(address)
        if number:
            address_line[-1] += f", {number}"
    elif number:
        address_line.append(number)

    if complement:
        address_line.append(complement)

    locality = _clean_route(location.get("locality"))
    if locality:
        address_line.append(locality)

    city = _clean_route(location.get("city"))
    state = _clean_route(location.get("state"))
    if city and state:
        address_line.append(f"{city}/{state}")
    elif city:
        address_line.append(city)
    elif state:
        address_line.append(state)

    zipcode = _clean_route(location.get("zipcode"))
    if zipcode:
        address_line.append(f"CEP {zipcode}")

    country = _clean_route(location.get("country"))
    if country and country.upper() not in {"BR", "BRA", "BRASIL"}:
        address_line.append(country)

    # Preserve order while removing duplicates.
    unique = []
    for item in address_line:
        if item and item not in unique:
            unique.append(item)

    return " • ".join(unique) or None


def _history_raw_event(
    shipment: Shipment,
    event: TrackingEvent,
) -> dict:
    raw = _safe_extra(shipment)
    return _matching_raw_event(
        _normalized_raw_events(raw),
        event.event_at,
    )


def _history_specific_location(raw_event: dict) -> str | None:
    location = raw_event.get("location") or {}
    if not isinstance(location, dict):
        return None

    if not any(
        location.get(key)
        for key in (
            "zipcode",
            "address",
            "locality",
            "number",
            "complement",
        )
    ):
        return None

    return _detailed_location(raw_event)


def _history_event_rows(
    shipment: Shipment,
    event: TrackingEvent,
    timezone_name: str,
) -> list[str]:
    raw_event = _history_raw_event(
        shipment,
        event,
    )

    source_time = _short_source_datetime(
        raw_event.get("createdAt")
    )
    date_text = (
        source_time
        or _short_datetime(
            event.event_at,
            timezone_name,
        )
    )

    description = (
        _compact_value(raw_event.get("description"))
        or _compact_value(event.description)
        or "Movimentação registrada"
    )

    origin = _clean_route(raw_event.get("from"))
    destination = _clean_route(raw_event.get("to"))
    detailed_location = _history_specific_location(
        raw_event
    )
    additional = _compact_value(
        raw_event.get("additionalInfo")
    )

    rows = [
        (
            "<tr><td>"
            f"<b>{html.escape(status_label(event.status))}</b><br>"
            f"<i>{html.escape(date_text)}</i>"
            "</td></tr>"
        ),
        (
            "<tr><td>"
            f"{html.escape(description)}"
            "</td></tr>"
        ),
    ]

    if origin and destination:
        rows.append(
            "<tr><td>"
            "📍 "
            f"<b>{html.escape(origin)}</b>"
            " → "
            f"<b>{html.escape(destination)}</b>"
            "</td></tr>"
        )
    elif event.location:
        rows.append(
            "<tr><td>"
            "📍 "
            f"<b>{html.escape(str(event.location))}</b>"
            "</td></tr>"
        )

    if detailed_location:
        rows.append(
            "<tr><td>"
            "🏢 "
            f"<i>{html.escape(detailed_location)}</i>"
            "</td></tr>"
        )

    if additional:
        rows.append(
            "<tr><td>"
            "ℹ️ "
            f"{html.escape(additional)}"
            "</td></tr>"
        )

    return rows


def format_tracking_history_rich_page(
    subscription: Subscription,
    shipment: Shipment,
    events: list[TrackingEvent],
    timezone_name: str,
    *,
    page: int = 0,
    events_per_page: int = 3,
) -> tuple[str, int, int]:
    if not events:
        return "", 0, 0

    events_per_page = max(1, events_per_page)
    total_pages = (
        len(events) + events_per_page - 1
    ) // events_per_page
    page = min(
        max(0, page),
        total_pages - 1,
    )

    start = page * events_per_page
    group = events[
        start:start + events_per_page
    ]

    nickname = html.escape(
        subscription.nickname
        or "Minha encomenda"
    )
    number = html.escape(
        shipment.tracking_number
    )

    _, _, eta, _ = extract_tracking_metadata(
        shipment,
        None,
    )

    page_label = (
        f" • {page + 1}/{total_pages}"
        if total_pages > 1
        else ""
    )

    top = (
        "<aside>"
        f"📋 <b>Histórico{page_label}</b><br>"
        f"🔎 <code>{number}</code><br>"
        f"<i>{nickname}</i>"
        "</aside>"
    )

    rows: list[str] = []

    if eta:
        rows.append(
            "<tr><td>"
            "📅 <b>Previsão de entrega:</b> "
            f"<i>{html.escape(eta)}</i>"
            "</td></tr>"
        )

    for index, event in enumerate(group):
        if index > 0:
            # Keep each tracking update visually separated in Telegram Rich
            # Messages without changing the content or grouping of the event.
            rows.append(
                "<tr><td><br></td></tr>"
            )

        rows.extend(
            _history_event_rows(
                shipment,
                event,
                timezone_name,
            )
        )

    return (
        top
        + "<table bordered>"
        + "".join(rows)
        + "</table>",
        page,
        total_pages,
    )


def format_tracking_history_fallback_page(
    subscription: Subscription,
    shipment: Shipment,
    events: list[TrackingEvent],
    timezone_name: str,
    *,
    page: int = 0,
    events_per_page: int = 3,
) -> tuple[str, int, int]:
    if not events:
        return "", 0, 0

    total_pages = (
        len(events) + events_per_page - 1
    ) // events_per_page
    page = min(
        max(0, page),
        total_pages - 1,
    )
    start = page * events_per_page
    group = events[
        start:start + events_per_page
    ]

    _, _, eta, _ = extract_tracking_metadata(
        shipment,
        None,
    )

    lines = [
        (
            "📋 <b>Histórico"
            + (
                f" • {page + 1}/{total_pages}"
                if total_pages > 1
                else ""
            )
            + "</b>"
        ),
        f"<code>{html.escape(shipment.tracking_number)}</code>",
        f"<i>{html.escape(subscription.nickname or 'Minha encomenda')}</i>",
    ]

    if eta:
        lines.extend([
            "",
            "📅 <b>Previsão de entrega:</b> "
            + f"<i>{html.escape(eta)}</i>",
        ])

    for event in group:
        raw_event = _history_raw_event(
            shipment,
            event,
        )
        source_time = _short_source_datetime(
            raw_event.get("createdAt")
        )
        time_text = (
            source_time
            or _short_datetime(
                event.event_at,
                timezone_name,
            )
        )

        description = (
            _compact_value(raw_event.get("description"))
            or event.description
            or "Movimentação registrada"
        )

        lines.extend([
            "",
            f"<b>{html.escape(status_label(event.status))}</b>",
            f"<i>{html.escape(time_text)}</i>",
            html.escape(description),
        ])

        origin = _clean_route(
            raw_event.get("from")
        )
        destination = _clean_route(
            raw_event.get("to")
        )

        if origin and destination:
            lines.append(
                "📍 "
                + html.escape(origin)
                + " → "
                + html.escape(destination)
            )
        elif event.location:
            lines.append(
                "📍 "
                + html.escape(event.location)
            )

        detailed = _history_specific_location(
            raw_event
        )
        if detailed:
            lines.append(
                "🏢 "
                + html.escape(detailed)
            )

        additional = _compact_value(
            raw_event.get("additionalInfo")
        )
        if additional:
            lines.append(
                "ℹ️ "
                + html.escape(additional)
            )

    return "\n".join(lines)[:3900], page, total_pages
