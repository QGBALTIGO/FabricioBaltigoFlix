from datetime import datetime

from app.models import TrackingEvent
from app.services.tracking import TrackingService


def _event(when: str, description: str) -> TrackingEvent:
    return TrackingEvent(
        shipment_id=1,
        event_hash=description,
        status="in_transit",
        status_raw="Em trânsito",
        description=description,
        location="BR",
        event_at=datetime.fromisoformat(when),
    )


def test_notification_filter_ignores_historical_backfill():
    previous = datetime.fromisoformat(
        "2026-09-18T16:40:56-03:00"
    )

    events = [
        _event(
            "2026-09-18T15:39:58-03:00",
            "Objeto postado",
        ),
        _event(
            "2026-09-21T08:49:19-03:00",
            "Objeto em transferência",
        ),
    ]

    fresh = TrackingService._notification_events_after(
        events,
        previous,
    )

    assert len(fresh) == 1
    assert fresh[0].description == (
        "Objeto em transferência"
    )
    assert fresh[0].event_at.isoformat() == (
        "2026-09-21T08:49:19-03:00"
    )


def test_notification_filter_rejects_same_timestamp_backfill():
    previous = datetime.fromisoformat(
        "2026-09-21T08:49:19-03:00"
    )

    events = [
        _event(
            "2026-09-21T08:49:19-03:00",
            "Mesmo evento com texto diferente",
        )
    ]

    assert (
        TrackingService._notification_events_after(
            events,
            previous,
        )
        == []
    )
