from types import SimpleNamespace

from app.bot.keyboards import (
    MAIN_MENU_TODAY,
    main_menu_keyboard,
    shipment_keyboard,
)


def _texts(markup):
    return [
        button.text
        for row in markup.inline_keyboard
        for button in row
    ]


def test_shipment_keyboard_has_only_requested_actions_when_enabled():
    sub = SimpleNamespace(
        id=7,
        shipment_id=11,
        notifications_enabled=True,
        notify_level="important",
    )

    markup = shipment_keyboard(
        sub,
        bot_username="MelhorRastreioBot",
        share_secret="secret",
    )
    texts = _texts(markup)

    assert texts == [
        "📋 Histórico",
        "🔔 Alertas",
        "🧭 O que fazer?",
        "🔗 Compartilhar rastreio",
    ]


def test_shipment_keyboard_alert_toggle_when_disabled():
    sub = SimpleNamespace(
        id=7,
        shipment_id=11,
        notifications_enabled=False,
        notify_level="off",
    )

    markup = shipment_keyboard(
        sub,
        bot_username="MelhorRastreioBot",
        share_secret="secret",
    )

    assert _texts(markup) == [
        "📋 Histórico",
        "🔔 Alertas",
        "🧭 O que fazer?",
        "🔗 Compartilhar rastreio",
    ]


def test_main_menu_exposes_today_dashboard():
    markup = main_menu_keyboard()
    texts = [
        button.text
        for row in markup.keyboard
        for button in row
    ]

    assert MAIN_MENU_TODAY == "🏠 Hoje"
    assert texts[0] == "🏠 Hoje"
    assert "📦 Meus pacotes" in texts


def test_shipment_keyboard_has_single_alert_entry_point():
    sub = SimpleNamespace(
        id=8,
        shipment_id=12,
        notifications_enabled=True,
        notify_level="all",
    )
    texts = _texts(
        shipment_keyboard(
            sub,
            bot_username="MelhorRastreioBot",
            share_secret="secret",
        )
    )

    assert texts.count("🔔 Alertas") == 1
    assert not any(
        "Ativar alerta" in text
        or "Desativar alerta" in text
        for text in texts
    )
