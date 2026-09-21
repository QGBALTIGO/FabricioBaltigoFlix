import json
from types import SimpleNamespace

from app.presentation import format_tracking_rich_html


def test_card_supports_rastreador_pacotes_route():
    shipment = SimpleNamespace(
        tracking_number="AP499229999BR",
        carrier_name="Correios",
        status="in_transit",
        last_event_at="2026-09-21T08:49:19-03:00",
        last_location="CURITIBA/PR",
        extra_json=json.dumps(
            {
                "estimatedDelivery": "2026-09-30",
                "tracking": [
                    {
                        "Posicoes": [
                            {
                                "Acao": (
                                    "Objeto em transferência - por favor aguarde"
                                ),
                                "Data": "2026-09-21 08:49:19",
                                "Detalhes": "",
                                "DetalhesFormatado": (
                                    "Objeto em transferência - por favor aguarde\n\r"
                                    "Saiu de Unidade de Tratamento em CURITIBA / PR "
                                    "para Unidade de Tratamento em CAMPO GRANDE / MS"
                                ),
                            }
                        ]
                    }
                ],
            }
        ),
    )
    sub = SimpleNamespace(
        nickname="Placa 10k",
    )

    rich = format_tracking_rich_html(
        sub,
        shipment,
        "America/Sao_Paulo",
    )

    assert "21 Set. - 08:49" in rich
    assert (
        "Unidade de Tratamento - CURITIBA/PR"
        in rich
    )
    assert (
        "Unidade de Tratamento - CAMPO GRANDE/MS"
        in rich
    )
    assert "30/09/2026" in rich
