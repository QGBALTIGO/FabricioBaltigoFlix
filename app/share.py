from __future__ import annotations

import hashlib
import hmac
import re

PAYLOAD_RE = re.compile(r"^s(\d+)_([a-f0-9]{12})$")


def make_share_payload(shipment_id: int, secret: str) -> str | None:
    if not secret:
        return None
    raw = str(shipment_id).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()[:12]
    return f"s{shipment_id}_{signature}"


def verify_share_payload(payload: str, secret: str) -> int | None:
    if not secret:
        return None
    match = PAYLOAD_RE.fullmatch(payload or "")
    if not match:
        return None
    shipment_id = int(match.group(1))
    expected = make_share_payload(shipment_id, secret)
    if expected and hmac.compare_digest(payload, expected):
        return shipment_id
    return None
