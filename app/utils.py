from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

TRACKING_RE = re.compile(
    r"^[A-Z0-9][A-Z0-9\-]{4,49}$",
    re.IGNORECASE,
)


def normalize_tracking_number(value: str) -> str:
    return re.sub(
        r"\s+",
        "",
        value or "",
    ).upper().strip()


def is_valid_tracking_number(value: str) -> bool:
    return bool(
        TRACKING_RE.fullmatch(
            normalize_tracking_number(value)
        )
    )


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
    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def parse_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return (
            value
            if value.tzinfo
            else value.replace(tzinfo=timezone.utc)
        )

    if not value:
        return datetime.now(timezone.utc)

    text = str(value).strip().replace(
        "Z",
        "+00:00",
    )

    for candidate in (
        text,
        text.replace(" ", "T"),
    ):
        try:
            dt = datetime.fromisoformat(candidate)
            return (
                dt
                if dt.tzinfo
                else dt.replace(tzinfo=timezone.utc)
            )
        except ValueError:
            pass

    return datetime.now(timezone.utc)


def mask_tracking_number(number: str) -> str:
    if len(number) <= 8:
        return number
    return f"{number[:4]}…{number[-4:]}"


def format_datetime(
    value: datetime,
    timezone_name: str,
) -> str:
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = timezone.utc

    return parse_datetime(value).astimezone(tz).strftime(
        "%d/%m/%Y %H:%M"
    )
