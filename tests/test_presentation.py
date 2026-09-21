import json
from types import SimpleNamespace

from app.presentation import format_tracking_card


def test_tracking_card_new_pattern():
    shipment = SimpleNamespace(
        tracking_number="AP499229999BR",
        carrier_name="Correios",
        status="in_transit",
        last_event_at=None,
        last_location="BALNEARIO CAMBORIU, SC",
        extra_json=json.dumps(
            {
                "estimatedDelivery": "2026-10-06T23:59:59-03:00",
                "trackingEvents": [
                    {
                        "createdAt": "2026-09-18T12:40:00-03:00",
                        "from": "01 - BALNEARIO CAMBORIU/SC",
                        "to": "01 - CURITIBA/PR",
                    }
                ],
            }
        ),
    )
    event = SimpleNamespace(
        status="in_transit",
        event_at="2026-09-18T12:40:00-03:00",
        location="BALNEARIO CAMBORIU, SC",
    )
    subscription = SimpleNamespace(
        nickname="Placa 10k",
    )

    text = format_tracking_card(
        subscription,
        shipment,
        "America/Sao_Paulo",
        event=event,
    )

    assert "AP499229999BR" in text
    assert "Placa 10k" in text
    assert "<b>Transportadora:</b> Correios" in text
    assert "Seu pacote está em movimentação." in text
    assert "BALNEARIO CAMBORIU/SC" in text
    assert "CURITIBA/PR" in text
    assert "06/10/2026" in text
    assert "Alertas" not in text
