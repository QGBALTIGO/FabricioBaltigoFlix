import json
from types import SimpleNamespace

from app.presentation import (
    format_tracking_card,
    format_tracking_rich_html,
)


def _shipment():
    return SimpleNamespace(
        tracking_number="AP499229999BR",
        carrier_name="Correios",
        status="in_transit",
        last_event_at="2026-09-18T16:40:00+00:00",
        last_location="BALNEARIO CAMBORIU, SC",
        extra_json=json.dumps({
            "estimatedDelivery": "2026-10-06T23:59:59-03:00",
            "trackingEvents": [{
                "createdAt": "2026-09-18 16:40:00",
                "from": "01 - BALNEARIO CAMBORIU/SC",
                "to": "01 - CURITIBA/PR",
            }],
        }),
    )


def test_tracking_card_uses_full_route_and_source_time():
    text = format_tracking_card(
        SimpleNamespace(nickname="Placa 10k"),
        _shipment(),
        "America/Campo_Grande",
    )
    assert "18 Set. - 16:40" in text
    assert "01 - BALNEARIO CAMBORIU/SC → 01 - CURITIBA/PR" in text
    assert "06/10/2026" in text


def test_tracking_rich_message_uses_native_bordered_table():
    rich = format_tracking_rich_html(
        SimpleNamespace(nickname="Placa 10k"),
        _shipment(),
        "America/Campo_Grande",
    )
    assert rich.startswith("<aside>")
    assert "<table bordered>" in rich
    assert "18 Set. - 16:40" in rich
    assert "01 - BALNEARIO CAMBORIU/SC" in rich
    assert "→" in rich
    assert "01 - CURITIBA/PR" in rich
    assert "06/10/2026" in rich


def test_correios_direct_metadata_fallback():
    shipment = SimpleNamespace(
        tracking_number="AP499229999BR",
        carrier_name="Correios",
        status="in_transit",
        last_event_at="2026-09-18T19:40:00+00:00",
        last_location="BALNEARIO CAMBORIU/SC",
        extra_json=json.dumps({
            "objetos": [{
                "dtPrevista": "2026-10-06",
                "eventos": [{
                    "dtHrCriado": "2026-09-18 16:40:00",
                    "unidade": {
                        "endereco": {
                            "cidade": "BALNEARIO CAMBORIU",
                            "uf": "SC",
                        }
                    },
                    "unidadeDestino": {
                        "endereco": {
                            "cidade": "CURITIBA",
                            "uf": "PR",
                        }
                    },
                }],
            }],
        }),
    )
    text = format_tracking_card(
        SimpleNamespace(nickname="Placa 10k"),
        shipment,
        "America/Sao_Paulo",
    )
    assert "16:40" in text
    assert "BALNEARIO CAMBORIU/SC" in text
    assert "CURITIBA/PR" in text
    assert "06/10/2026" in text
