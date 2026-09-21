from __future__ import annotations

import hashlib
import hmac
import re

PAYLOAD_RE = re.compile(
    r"^s(\d+)_([a-f0-9]{12}|[a-f0-9]{16})$"
)


def _signature(
    shipment_id: int,
    secret: str,
    length: int,
) -> str:
    raw = str(
        shipment_id
    ).encode("utf-8")

    return hmac.new(
        secret.encode("utf-8"),
        raw,
        hashlib.sha256,
    ).hexdigest()[:length]


def make_share_payload(
    shipment_id: int,
    secret: str,
) -> str | None:
    if not secret:
        return None

    signature = _signature(
        shipment_id,
        secret,
        16,
    )
    return (
        f"s{shipment_id}_{signature}"
    )


def verify_share_payload(
    payload: str,
    secret: str,
) -> int | None:
    if not secret:
        return None

    match = PAYLOAD_RE.fullmatch(
        payload or ""
    )
    if not match:
        return None

    shipment_id = int(
        match.group(1)
    )
    provided = match.group(2)
    expected = _signature(
        shipment_id,
        secret,
        len(provided),
    )

    if hmac.compare_digest(
        provided,
        expected,
    ):
        return shipment_id

    return None
