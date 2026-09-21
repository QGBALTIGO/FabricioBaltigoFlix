from datetime import datetime, timedelta, timezone

from app.share import make_share_payload, verify_share_payload
from app.status import status_explanation
from app.utils import humanize_age, is_stale, mentions_payment


def test_signed_share_payload():
    payload = make_share_payload(123, "super-secret")
    assert payload
    assert verify_share_payload(payload, "super-secret") == 123
    assert verify_share_payload(payload, "wrong") is None
    assert (
        verify_share_payload(
            "s999_deadbeefdead",
            "super-secret",
        )
        is None
    )


def test_stale_helpers():
    old = datetime.now(timezone.utc) - timedelta(hours=80)
    assert is_stale(old, 72)
    assert "dia" in humanize_age(old)


def test_security_signal_and_explanation():
    assert mentions_payment(
        "Pague uma taxa via PIX para liberação"
    )
    assert not mentions_payment(
        "Objeto encaminhado para unidade"
    )
    assert (
        "canal oficial"
        in status_explanation("customs").lower()
    )
