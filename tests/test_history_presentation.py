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
                    "createdAt": "2026-09-18 16:40:00",
                    "description": "Objeto em transferência - por favor aguarde",
                    "from": "01 - BALNEARIO CAMBORIU/SC",
                    "to": "01 - CURITIBA/PR",
                    "location": {
                        "city": "BALNEARIO CAMBORIU",
                        "state": "SC",
                        "country": "BR",
                    },
                }
            ],
        }),
    )


def test_history_looks_like_current_state_card():
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
    )

    assert page == 0
    assert pages == 1
    assert rich.startswith("<aside>")
    assert rich.count("<table bordered>") == 1
    assert "📅 <b>Previsão de entrega:</b>" in rich
    assert "06/10/2026" in rich
    assert "<b>🚚 Em trânsito</b><br><i>18 Set. - 16:40</i>" in rich
    assert "Objeto em transferência - por favor aguarde" in rich
    assert "01 - BALNEARIO CAMBORIU/SC" in rich
    assert "01 - CURITIBA/PR" in rich
    assert "Correios" not in rich


def test_history_uses_three_events_per_page():
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
        for i in range(7)
    ]

    rich, page, pages = format_tracking_history_rich_page(
        subscription,
        shipment,
        events,
        "America/Sao_Paulo",
        page=1,
    )

    assert page == 1
    assert pages == 3
    assert "2/3" in rich
    # 1 ETA row + 3 events * 3 rows + 2 blank separator rows.
    assert rich.count("<tr><td>") == 12
    assert rich.count("<tr><td><br></td></tr>") == 2
