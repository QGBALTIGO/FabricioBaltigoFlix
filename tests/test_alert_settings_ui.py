from types import SimpleNamespace

from app.bot.smart import _alert_menu_markup


def _texts(markup):
    return [
        button.text
        for row in markup.inline_keyboard
        for button in row
    ]


def test_alert_settings_have_no_quiet_hours():
    sub = SimpleNamespace(
        id=7,
        notifications_enabled=True,
    )
    pref = SimpleNamespace(
        custom_alerts_enabled=True,
        alert_intermediate=True,
        alert_out_for_delivery=True,
        alert_problems=True,
        alert_delivered=True,
    )

    texts = _texts(
        _alert_menu_markup(
            sub,
            pref,
        )
    )

    assert "🔕 Desativar todos" in texts
    assert "✅ Movimentações" in texts
    assert "✅ Saiu para entrega" in texts
    assert "✅ Problemas/retirada" in texts
    assert "✅ Entregue" in texts
    assert not any(
        "🌙" in text
        or "silêncio" in text.lower()
        for text in texts
    )


def test_alert_settings_master_button_enables_all():
    sub = SimpleNamespace(
        id=7,
        notifications_enabled=False,
    )
    pref = SimpleNamespace(
        custom_alerts_enabled=False,
        alert_intermediate=True,
        alert_out_for_delivery=True,
        alert_problems=True,
        alert_delivered=True,
    )

    texts = _texts(
        _alert_menu_markup(
            sub,
            pref,
        )
    )

    assert "🔔 Ativar alertas" in texts
