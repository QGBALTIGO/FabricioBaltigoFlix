from app.models import SubscriptionPreference
from app.services.preferences import custom_alert_allows


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
