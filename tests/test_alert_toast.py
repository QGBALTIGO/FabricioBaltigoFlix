from app.bot.handlers import _alert_toggle_toast


def test_alert_toggle_toast_enabled():
    text = _alert_toggle_toast(True)
    assert "🔔 Alertas ativados!" in text
    assert "novas movimentações" in text


def test_alert_toggle_toast_disabled():
    text = _alert_toggle_toast(False)
    assert "🔕 Alertas desativados." in text
    assert "não receberá novas atualizações" in text
