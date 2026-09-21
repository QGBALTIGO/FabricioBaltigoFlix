from datetime import datetime, timezone
from types import SimpleNamespace

from app.bot.handlers import (
    _add_package_help_text,
    _my_packages_text,
    _remove_packages_text,
)
from app.bot.keyboards import (
    MAIN_MENU_REMOVE_PACKAGE,
)
from app.bot.messages import (
    HELP,
    INVALID_CODE,
    NO_SHIPMENTS,
    SECURITY,
    WELCOME,
)
from app.presentation import (
    format_tracking_notification_fallback,
    format_tracking_notification_rich_html,
)
from app.status import STATUS_EXPLANATIONS


def _public_messages():
    return [
        WELCOME,
        HELP,
        SECURITY,
        NO_SHIPMENTS,
        INVALID_CODE,
        _my_packages_text(1),
        _my_packages_text(3),
        _remove_packages_text(1),
        _remove_packages_text(3),
        _add_package_help_text(),
    ]


def test_public_copy_has_no_placeholder_plural_notation():
    for text in _public_messages():
        assert "encomenda(s)" not in text
        assert "pacote(s)" not in text
        assert "rastreio(s)" not in text
        assert "dia(s)" not in text


def test_main_help_uses_image_language_instead_of_print_only():
    combined = "\n".join(
        [
            WELCOME,
            HELP,
            INVALID_CODE,
        ]
    ).lower()

    assert "foto" in combined
    assert "imagem" in combined
    assert " print" not in combined


def test_status_explanations_use_encomenda_consistently():
    for explanation in STATUS_EXPLANATIONS.values():
        assert "pacote" not in explanation.lower()


def test_remove_menu_is_short_and_remove_copy_uses_encomenda():
    assert MAIN_MENU_REMOVE_PACKAGE == "🗑 Remover"
    assert "Remover encomenda" in _remove_packages_text(1)
    assert "pacote salvo" not in _remove_packages_text(1).lower()


def test_tracking_notifications_do_not_reintroduce_action_advice():
    shipment = SimpleNamespace(
        tracking_number="AB123456789BR",
        carrier_name="Correios",
        extra_json=None,
    )
    subscription = SimpleNamespace(
        nickname="Teclado",
    )
    event = SimpleNamespace(
        status="in_transit",
        description="Objeto em transferência.",
        location="Campo Grande/MS",
        event_at=datetime(
            2026,
            9,
            21,
            18,
            30,
            tzinfo=timezone.utc,
        ),
    )

    rich = format_tracking_notification_rich_html(
        subscription,
        shipment,
        event,
        "America/Campo_Grande",
    )
    fallback = format_tracking_notification_fallback(
        subscription,
        shipment,
        event,
        "America/Campo_Grande",
    )

    for text in (
        rich,
        fallback,
    ):
        assert "💡" not in text
        assert "Agora:" not in text
        assert "Nova atualização" in text
