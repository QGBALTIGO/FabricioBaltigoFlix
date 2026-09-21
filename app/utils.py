from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

TRACKING_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-]{4,49}$", re.IGNORECASE)
SUSPICIOUS_PAYMENT_RE = re.compile(
    r"\b(pix|boleto|taxa|pagamento|pague|cobrança|cobranca|liberação mediante|liberacao mediante)\b",
    re.IGNORECASE,
)


def normalize_tracking_number(value: str) -> str:
    return re.sub(r"\s+", "", value or "").upper().strip()


def is_valid_tracking_number(value: str) -> bool:
    return bool(TRACKING_RE.fullmatch(normalize_tracking_number(value)))


def event_hash(
    tracking_number: str,
    status: str,
    description: str,
    location: str | None,
    event_at: datetime,
) -> str:
    raw = "|".join(
        [
            normalize_tracking_number(tracking_number),
            status or "",
            (description or "").strip(),
            (location or "").strip(),
            event_at.astimezone(timezone.utc).isoformat(),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    if not value:
        return datetime.now(timezone.utc)

    text = str(value).strip().replace("Z", "+00:00")
    for candidate in (text, text.replace(" ", "T")):
        try:
            dt = datetime.fromisoformat(candidate)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    return datetime.now(timezone.utc)


def parse_datetime_assuming_timezone(
    value,
    timezone_name: str,
) -> datetime:
    """Parse a timestamp, attaching timezone_name only when the source is naive."""
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = timezone.utc

    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=tz)

    if not value:
        return datetime.now(timezone.utc)

    text = str(value).strip().replace("Z", "+00:00")
    for candidate in (text, text.replace(" ", "T")):
        try:
            dt = datetime.fromisoformat(candidate)
            return dt if dt.tzinfo else dt.replace(tzinfo=tz)
        except ValueError:
            pass

    return parse_datetime(value)


def mask_tracking_number(number: str) -> str:
    if len(number) <= 8:
        return number
    return f"{number[:4]}…{number[-4:]}"


def format_datetime(value: datetime, timezone_name: str) -> str:
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = timezone.utc
    return parse_datetime(value).astimezone(tz).strftime("%d/%m/%Y %H:%M")


def age_hours(value: datetime | None) -> float | None:
    if not value:
        return None
    dt = parse_datetime(value)
    return max(
        0.0,
        (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600,
    )


def humanize_age(value: datetime | None) -> str:
    hours = age_hours(value)
    if hours is None:
        return "sem atualização registrada"
    if hours < 1:
        minutes = max(1, int(hours * 60))
        return f"há {minutes} min"
    if hours < 24:
        return f"há {int(hours)} h"
    days = int(hours // 24)
    return f"há {days} dia" + ("s" if days != 1 else "")


def is_stale(value: datetime | None, threshold_hours: int) -> bool:
    hours = age_hours(value)
    return hours is not None and hours >= threshold_hours


def mentions_payment(text: str | None) -> bool:
    return bool(SUSPICIOUS_PAYMENT_RE.search(text or ""))



def parse_tracking_input(value: str) -> tuple[str | None, str | None]:
    text = (value or "").strip()
    if not text:
        return None, None

    parts = text.split(maxsplit=1)
    number = normalize_tracking_number(parts[0])
    if not is_valid_tracking_number(number):
        return None, None

    nickname = None
    if len(parts) > 1:
        nickname = parts[1].strip()[:120] or None

    return number, nickname
