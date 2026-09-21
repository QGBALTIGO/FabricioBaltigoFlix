import json
from types import SimpleNamespace

from app.presentation import (
    format_tracking_history_rich_chunks,
)


def test_rich_history_uses_source_details_and_full_route():
    shipment = SimpleNamespace(
        tracking_number="AP499229999BR",
        carrier_name="Correios",
        extra_json=json.dumps({
            "trackingEvents": [
                {
                    "trackerType": "correios",
                    "trackingCode": "AP499229999BR",
                    "createdAt": "2026-09-18 16:40:00",
                    "title": "Objeto em transferência",
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
            ]
        }),
    )
    subscription = SimpleNamespace(
        nickname="Placa 10k",
    )
    event = SimpleNamespace(
        status="in_transit",
        event_at="2026-09-18T19:40:00+00:00",
        description="Objeto em transferência - por favor aguarde",
        location="BALNEARIO CAMBORIU, SC",
    )

    chunks = format_tracking_history_rich_chunks(
        subscription,
        shipment,
        [event],
        "America/Sao_Paulo",
    )

    assert len(chunks) == 1
    rich = chunks[0]
    assert rich.startswith("<aside>")
    assert "<table bordered>" in rich
    assert "Histórico" in rich
    assert "AP499229999BR" in rich
    assert "18 Set. - 16:40" in rich
    assert "01 - BALNEARIO CAMBORIU/SC" in rich
    assert "01 - CURITIBA/PR" in rich
    assert "Rua Exemplo, 100" in rich
    assert "Centro" in rich
    assert "CEP 88330-000" in rich
    assert "Transferência para unidade de destino" in rich


def test_rich_history_chunks_many_events():
    shipment = SimpleNamespace(
        tracking_number="AB123456789BR",
        carrier_name="Correios",
        extra_json=json.dumps({
            "trackingEvents": []
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
        for i in range(7)
    ]

    chunks = format_tracking_history_rich_chunks(
        subscription,
        shipment,
        events,
        "America/Sao_Paulo",
        events_per_chunk=5,
    )

    assert len(chunks) == 2
    assert "1/2" in chunks[0]
    assert "2/2" in chunks[1]
