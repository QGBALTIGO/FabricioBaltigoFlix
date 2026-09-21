from datetime import datetime, timezone

from app.models import (
    SubscriptionPreference,
    UserPreference,
)
from app.services.preferences import (
    custom_alert_allows,
    quiet_label,
    quiet_window,
)


def test_custom_alerts_preserve_all_events_until_enabled():
    pref = SubscriptionPreference(
        subscription_id=1,
        custom_alerts_enabled=False,
        alert_intermediate=False,
    )
    assert custom_alert_allows(pref, "in_transit") is True


def test_custom_alert_categories():
    pref = SubscriptionPreference(
        subscription_id=1,
        custom_alerts_enabled=True,
        alert_intermediate=False,
        alert_out_for_delivery=True,
        alert_delivered=True,
        alert_problems=False,
    )

    assert custom_alert_allows(pref, "in_transit") is False
    assert custom_alert_allows(pref, "out_for_delivery") is True
    assert custom_alert_allows(pref, "delivered") is True
    assert custom_alert_allows(pref, "customs") is False


def test_quiet_window_crosses_midnight():
    pref = UserPreference(
        user_id=1,
        quiet_hours_enabled=True,
        quiet_start_minute=23 * 60,
        quiet_end_minute=7 * 60,
    )

    # 03:00 UTC = 23:00 previous day in Campo Grande only seasonally,
    # so use São Paulo explicitly and a fixed time inside the window.
    now = datetime(
        2026,
        9,
        22,
        5,
        0,
        tzinfo=timezone.utc,
    )
    quiet, deliver_after = quiet_window(
        pref,
        "America/Sao_Paulo",
        now=now,
    )

    assert quiet is True
    assert deliver_after is not None
    assert deliver_after > now
    assert quiet_label(pref) == "23:00–07:00"


def test_quiet_window_outside_period():
    pref = UserPreference(
        user_id=1,
        quiet_hours_enabled=True,
        quiet_start_minute=23 * 60,
        quiet_end_minute=7 * 60,
    )

    quiet, deliver_after = quiet_window(
        pref,
        "America/Sao_Paulo",
        now=datetime(
            2026,
            9,
            21,
            15,
            0,
            tzinfo=timezone.utc,
        ),
    )

    assert quiet is False
    assert deliver_after is None
