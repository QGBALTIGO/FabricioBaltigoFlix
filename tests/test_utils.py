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


def test_handler_exposes_tracking_input_parser():
    from app.bot import handlers

    assert handlers.parse_tracking_input(
        "AP499229999BR Placa 10k"
    ) == (
        "AP499229999BR",
        "Placa 10k",
    )



def test_parse_datetime_assuming_timezone_for_naive_brazil_time():
    from datetime import timedelta

    from app.utils import parse_datetime_assuming_timezone

    dt = parse_datetime_assuming_timezone(
        "2026-09-18 16:40:00",
        "America/Sao_Paulo",
    )

    assert dt.hour == 16
    assert dt.utcoffset() == timedelta(hours=-3)
