from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Shipment, TrackingEvent
from app.utils import age_hours

ATTENTION_STATUSES = {
    "customs",
    "available_for_pickup",
    "delivery_failed",
    "exception",
    "returned",
}

ACTIVE_TRANSIT_STATUSES = {
    "unknown",
    "info_received",
    "picked_up",
    "in_transit",
}


@dataclass(frozen=True)
class DeliveryEstimate:
    start: datetime
    end: datetime
    sample_size: int
    confidence: str
    source: str


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * max(0.0, min(1.0, fraction))
    low = int(position)
    high = min(len(ordered) - 1, low + 1)
    weight = position - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


async def estimate_delivery_window(
    session: AsyncSession,
    shipment: Shipment,
) -> DeliveryEstimate | None:
    if shipment.status == "delivered":
        return None

    carrier = str(shipment.carrier_name or "").strip()
    provider = str(shipment.provider or "").strip()
    if not carrier and not provider:
        return None

    carrier_filter = (
        Shipment.carrier_name == carrier
        if carrier
        else Shipment.provider == provider
    )

    rows = (
        await session.execute(
            select(
                Shipment.id,
                func.max(TrackingEvent.event_at),
                Shipment.delivered_at,
            )
            .join(
                TrackingEvent,
                TrackingEvent.shipment_id == Shipment.id,
            )
            .where(
                Shipment.id != shipment.id,
                Shipment.delivered_at.is_not(None),
                carrier_filter,
                TrackingEvent.status == shipment.status,
            )
            .group_by(
                Shipment.id,
                Shipment.delivered_at,
            )
            .order_by(
                Shipment.delivered_at.desc(),
            )
            .limit(120)
        )
    ).all()

    remaining_seconds: list[float] = []
    for _, status_at, delivered_at in rows:
        if not status_at or not delivered_at:
            continue
        delta = (delivered_at - status_at).total_seconds()
        if 30 * 60 <= delta <= 45 * 24 * 3600:
            remaining_seconds.append(float(delta))

    source = "status"
    if len(remaining_seconds) < 5:
        total_rows = (
            await session.execute(
                select(
                    Shipment.registered_at,
                    Shipment.delivered_at,
                )
                .where(
                    Shipment.id != shipment.id,
                    Shipment.delivered_at.is_not(None),
                    carrier_filter,
                )
                .order_by(
                    Shipment.delivered_at.desc(),
                )
                .limit(120)
            )
        ).all()
        remaining_seconds = []
        for registered_at, delivered_at in total_rows:
            if not registered_at or not delivered_at:
                continue
            delta = (delivered_at - registered_at).total_seconds()
            if 2 * 3600 <= delta <= 60 * 24 * 3600:
                remaining_seconds.append(float(delta))
        source = "carrier_total"

    if len(remaining_seconds) < 5:
        return None

    now = datetime.now(timezone.utc)
    reference = shipment.last_event_at or shipment.registered_at
    elapsed = 0.0
    if source == "status" and reference:
        ref = reference
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)
        elapsed = max(0.0, (now - ref.astimezone(timezone.utc)).total_seconds())

    low = max(60 * 60, _percentile(remaining_seconds, 0.30) - elapsed)
    high = max(low, _percentile(remaining_seconds, 0.70) - elapsed)

    # Avoid showing an unrealistically narrow interval from sparse data.
    if high - low < 3 * 3600:
        high = low + 3 * 3600

    sample_size = len(remaining_seconds)
    confidence = (
        "alta"
        if sample_size >= 30
        else "média"
        if sample_size >= 12
        else "inicial"
    )

    return DeliveryEstimate(
        start=now + timedelta(seconds=low),
        end=now + timedelta(seconds=high),
        sample_size=sample_size,
        confidence=confidence,
        source=source,
    )


def format_delivery_estimate(
    estimate: DeliveryEstimate | None,
    timezone_name: str,
) -> str | None:
    if estimate is None:
        return None

    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = timezone.utc

    start = estimate.start.astimezone(tz)
    end = estimate.end.astimezone(tz)

    if start.date() == end.date():
        window = start.strftime("%d/%m")
        if start.hour != end.hour:
            window += f" entre {start:%Hh} e {end:%Hh}"
    else:
        window = f"{start:%d/%m}–{end:%d/%m}"

    return (
        f"{window} • confiança {estimate.confidence} "
        f"({estimate.sample_size} entregas semelhantes)"
    )


def package_bucket(
    shipment: Shipment,
    *,
    stale_after_hours: int,
    now: datetime | None = None,
    timezone_name: str = "America/Sao_Paulo",
) -> str:
    if shipment.status == "out_for_delivery":
        return "out_for_delivery"

    if shipment.status in ATTENTION_STATUSES:
        return "attention"

    reference = shipment.last_event_at or shipment.registered_at
    hours = age_hours(reference)
    if (
        shipment.status != "delivered"
        and hours is not None
        and hours >= stale_after_hours
    ):
        return "attention"

    if shipment.status == "delivered":
        current = now or datetime.now(timezone.utc)
        try:
            tz = ZoneInfo(timezone_name)
        except Exception:
            tz = timezone.utc

        delivered = shipment.delivered_at
        if delivered:
            if delivered.tzinfo is None:
                delivered = delivered.replace(tzinfo=timezone.utc)
            if delivered.astimezone(tz).date() == current.astimezone(tz).date():
                return "delivered_today"
        return "delivered"

    if shipment.status == "arrived_destination":
        return "near"

    return "transit"


def action_advice(status: str) -> tuple[str, str]:
    advice = {
        "unknown": (
            "Aguarde a primeira leitura",
            "Códigos novos podem demorar algumas horas para aparecer. O bot continuará consultando automaticamente.",
        ),
        "info_received": (
            "A postagem ainda pode estar começando",
            "A etiqueta foi criada ou os dados foram recebidos. Ainda pode faltar a coleta física do pacote.",
        ),
        "picked_up": (
            "Nenhuma ação necessária",
            "A encomenda já entrou na operação da transportadora. Continue acompanhando normalmente.",
        ),
        "in_transit": (
            "Continue acompanhando",
            "Nem toda transferência entre centros gera uma leitura. Só vale agir se o prazo prometido passar ou surgir uma ocorrência.",
        ),
        "arrived_destination": (
            "Fique atento às próximas horas",
            "O pacote chegou à região de destino e normalmente a próxima etapa é separação local ou saída para entrega.",
        ),
        "out_for_delivery": (
            "Prepare o recebimento",
            "Se possível, deixe alguém disponível no endereço e acompanhe o telefone cadastrado no pedido.",
        ),
        "available_for_pickup": (
            "Verifique onde retirar",
            "Confira a unidade indicada no rastreio, o prazo de retirada e leve documento com foto. Algumas transportadoras também exigem o código do objeto.",
        ),
        "delivery_failed": (
            "Confira o motivo da tentativa",
            "Revise endereço, ausência do destinatário e instruções da transportadora. Pode haver nova tentativa ou retirada em unidade.",
        ),
        "customs": (
            "Use apenas canais oficiais",
            "Aguarde a decisão fiscal e confirme qualquer tributo somente no site/app oficial da transportadora ou órgão competente. Desconfie de links de cobrança recebidos por mensagem.",
        ),
        "exception": (
            "Leia a ocorrência antes de agir",
            "Problemas de endereço, avaria, retenção ou extravio exigem ações diferentes. Use o texto da movimentação e procure o canal oficial da transportadora se a ocorrência persistir.",
        ),
        "returned": (
            "Fale com a loja ou remetente",
            "O fluxo de devolução foi iniciado. Confirme reenvio, correção de endereço ou reembolso diretamente com quem fez a postagem.",
        ),
        "delivered": (
            "Confirme o recebimento",
            "Se você não reconhece a entrega, verifique portaria, vizinhos e comprovante da transportadora antes de abrir uma contestação.",
        ),
    }
    return advice.get(
        str(status or "unknown"),
        advice["unknown"],
    )
