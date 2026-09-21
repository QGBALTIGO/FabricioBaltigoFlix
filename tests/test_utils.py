from app.utils import (
    is_valid_tracking_number,
    normalize_tracking_number,
    parse_tracking_input,
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


def test_parse_tracking_input_code_only():
    assert parse_tracking_input(
        "PN123456789BR"
    ) == ("PN123456789BR", None)


def test_parse_tracking_input_with_name():
    assert parse_tracking_input(
        "pn123456789br Minha encomenda 😊🚚"
    ) == (
        "PN123456789BR",
        "Minha encomenda 😊🚚",
    )


def test_parse_tracking_input_rejects_bad_code():
    assert parse_tracking_input(
        "isso não é um código"
    ) == (None, None)
