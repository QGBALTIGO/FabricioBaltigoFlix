from app.providers.seventeen_track import (
    SeventeenTrackProvider,
)


def test_parse_v24_webhook_shape():
    payload = {
        "event": "TRACKING_UPDATED",
        "data": {
            "number": "RR123456789CN",
            "carrier": 3011,
            "track_info": {
                "latest_status": {
                    "status": "InTransit",
                    "sub_status": "InTransit_Other",
                },
                "tracking": {
                    "providers": [
                        {
                            "provider": {
                                "key": 3011,
                                "name": "China Post",
                            },
                            "events": [
                                {
                                    "time_iso": (
                                        "2026-09-21T10:00:00-04:00"
                                    ),
                                    "description": (
                                        "Objeto encaminhado"
                                    ),
                                    "location": (
                                        "Campo Grande, MS"
                                    ),
                                    "stage": "InTransit",
                                }
                            ],
                        }
                    ]
                },
            },
        },
    }

    items = SeventeenTrackProvider.parse_webhook(
        payload
    )

    assert len(items) == 1

    item = items[0]

    assert (
        item.tracking_number
        == "RR123456789CN"
    )
    assert item.carrier_name == "China Post"
    assert item.status_raw == "InTransit"
    assert (
        item.events[0].status
        == "in_transit"
    )
