import json
from types import SimpleNamespace

from app.presentation import (
    format_tracking_history_rich_page,
)


def _shipment():
    return SimpleNamespace(
        tracking_number="AP499229999BR",
        carrier_name="Correios",
        extra_json=json.dumps({
            "estimatedDelivery": "2026-10-06T23:59:59-03:00",
            "trackingEvents": [
                {
                    "trackerType": "correios",
                    "trackingCode": "AP499229999BR",
                    "createdAt": "2026-09-18 16:40:00",
                    "description": "Objeto em transferência - por favor aguarde",
                    "from": "01 - BALNEARIO CAMBORIU/SC",
                    "to": "01 - CURITIBA/PR",
                    "location": {
                        "zipcode": "88330-000",
                        "address": "Rua Exemplo",
                        "locality": "Centro",
                        "number": "100",
                        "complement": "Unidade logística",
                        "city": "BALNEARIO CAMBORIU",
                        "state": "SC",
                        "country": "BR",
                    },
                    "additionalInfo": "Transferência para unidade de destino",
                }
            ],
        }),
    )


def test_history_is_compact_and_has_eta_without_carrier_repetition():
    shipment = _shipment()
    subscription = SimpleNamespace(
        nickname="Placa 10k",
    )
    event = SimpleNamespace(
        status="in_transit",
        event_at="2026-09-18T19:40:00+00:00",
        description="Objeto em transferência - por favor aguarde",
        location="BALNEARIO CAMBORIU, SC",
    )

    rich, page, pages = format_tracking_history_rich_page(
        subscription,
        shipment,
        [event],
        "America/Sao_Paulo",
        page=0,
        events_per_page=4,
    )

    assert page == 0
    assert pages == 1
    assert rich.startswith("<aside>")
    assert rich.count("<table bordered>") == 1
    assert rich.count("<tr><td>") == 1
    assert "Placa 10k" in rich
    assert "AP499229999BR" in rich
    assert "06/10/2026" in rich
    assert "18 Set. - 16:40" in rich
    assert "01 - BALNEARIO CAMBORIU/SC" in rich
    assert "01 - CURITIBA/PR" in rich
    assert "Rua Exemplo, 100" in rich
    assert "CEP 88330-000" in rich
    assert "Transferência para unidade de destino" in rich
    assert "🚚 correios" not in rich.lower()
    assert "🚚 Correios" not in rich


def test_history_does_not_duplicate_city_when_no_specific_address():
    shipment = SimpleNamespace(
        tracking_number="AP499229999BR",
        carrier_name="Correios",
        extra_json=json.dumps({
            "estimatedDelivery": "2026-10-06",
            "trackingEvents": [{
                "createdAt": "2026-09-18 16:40:00",
                "description": "Objeto postado",
                "from": "BALNEARIO CAMBORIU/SC",
                "to": "CURITIBA/PR",
                "location": {
                    "city": "BALNEARIO CAMBORIU",
                    "state": "SC",
                    "country": "BR",
                },
            }],
        }),
    )
    event = SimpleNamespace(
        status="picked_up",
        event_at="2026-09-18T19:40:00+00:00",
        description="Objeto postado",
        location="BALNEARIO CAMBORIU, SC",
    )

    rich, _, _ = format_tracking_history_rich_page(
        SimpleNamespace(nickname="Teste"),
        shipment,
        [event],
        "America/Sao_Paulo",
    )

    assert "🏢" not in rich


def test_history_paginates_in_place():
    shipment = SimpleNamespace(
        tracking_number="AB123456789BR",
        carrier_name="Correios",
        extra_json=json.dumps({
            "estimatedDelivery": "2026-10-06",
            "trackingEvents": [],
        }),
    )
    subscription = SimpleNamespace(
        nickname="Teste",
    )
    events = [
        SimpleNamespace(
            status="in_transit",
            event_at=f"2026-09-{18-i:02d}T10:00:00-03:00",
            description=f"Evento {i}",
            location="SC",
        )
        for i in range(9)
    ]

    rich, page, pages = format_tracking_history_rich_page(
        subscription,
        shipment,
        events,
        "America/Sao_Paulo",
        page=1,
        events_per_page=4,
    )

    assert page == 1
    assert pages == 3
    assert "2/3" in rich
    assert rich.count("<tr><td>") == 4
