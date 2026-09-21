import hashlib
import hmac

from app.share import (
    make_share_payload,
    verify_share_payload,
)


def test_new_share_payload_uses_64_bit_signature():
    payload = make_share_payload(
        123,
        "segredo-forte",
    )

    assert payload is not None
    assert len(
        payload.split("_", 1)[1]
    ) == 16
    assert verify_share_payload(
        payload,
        "segredo-forte",
    ) == 123


def test_old_12_hex_share_links_still_work():
    shipment_id = 123
    secret = "segredo-forte"
    old_signature = hmac.new(
        secret.encode(),
        str(shipment_id).encode(),
        hashlib.sha256,
    ).hexdigest()[:12]

    old_payload = (
        f"s{shipment_id}_{old_signature}"
    )

    assert verify_share_payload(
        old_payload,
        secret,
    ) == shipment_id
