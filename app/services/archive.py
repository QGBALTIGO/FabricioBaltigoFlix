from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func

from app.models import Shipment
from app.utils import parse_datetime


def delivered_reference(
    shipment: Shipment,
) -> datetime | None:
    return (
        shipment.delivered_at
        or shipment.last_event_at
        or shipment.registered_at
    )


def delivered_reference_sql():
    return func.coalesce(
        Shipment.delivered_at,
        Shipment.last_event_at,
        Shipment.registered_at,
    )


def archive_cutoff(
    days: int,
    *,
    now: datetime | None = None,
) -> datetime:
    current = now or datetime.now(
        timezone.utc
    )
    if current.tzinfo is None:
        current = current.replace(
            tzinfo=timezone.utc
        )
    return current.astimezone(
        timezone.utc
    ) - timedelta(
        days=max(
            1,
            int(days),
        )
    )


def delivered_age_label(
    shipment: Shipment,
    timezone_name: str,
    *,
    now: datetime | None = None,
) -> str:
    reference = delivered_reference(
        shipment
    )
    if reference is None:
        return "entrega concluída"

    try:
        tz = ZoneInfo(
            timezone_name
        )
    except Exception:
        tz = timezone.utc

    current = now or datetime.now(
        timezone.utc
    )
    if current.tzinfo is None:
        current = current.replace(
            tzinfo=timezone.utc
        )

    delivered = parse_datetime(
        reference
    )
    days = max(
        0,
        (
            current.astimezone(tz).date()
            - delivered.astimezone(tz).date()
        ).days,
    )

    if days == 0:
        return "entregue hoje"
    if days == 1:
        return "entregue ontem"
    return f"entregue há {days} dias"
