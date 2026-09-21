from app.utils import (
    is_valid_tracking_number,
    normalize_tracking_number,
)


def test_normalize_tracking_number():
    assert normalize_tracking_number(
        "  nm 123 456 br "
    ) == "NM123456BR"


def test_tracking_validation():
    assert is_valid_tracking_number(
        "NM123456789BR"
    )
    assert is_valid_tracking_number(
        "12345"
    )
    assert not is_valid_tracking_number(
        "1234"
    )
    assert not is_valid_tracking_number(
        "abc@123"
    )
