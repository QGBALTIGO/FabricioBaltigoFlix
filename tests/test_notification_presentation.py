import json
from types import SimpleNamespace

from app.presentation import (
    format_tracking_notification_rich_html,
)


def _sub():
    return SimpleNamespace(
        nickname="Placa 100k",
    )


def test_notification_real_update_is_standardized():
    shipment = SimpleNamespace(
        tracking_number="AP499229999BR",
        carrier_name="Correios",
        extra_json=json.dumps(
            {
                "estimatedDelivery": "2026-10-06",
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
    event = SimpleNamespace(
        status="in_transit",
        event_at="2026-09-21T08:49:19-03:00",
        description=(
            "Objeto em transferência - por favor aguarde"
        ),
        location="CURITIBA/PR",
    )

    rich = format_tracking_notification_rich_html(
        _sub(),
        shipment,
        event,
        "America/Sao_Paulo",
    )

    assert "🔔 <b>Nova atualização</b>" in rich
    assert "AP499229999BR" in rich
    assert "Placa 100k" in rich
    assert "🚚 Em trânsito" in rich
    assert "21 Set. - 08:49" in rich
    assert "CURITIBA/PR" in rich
    assert "CAMPO GRANDE/MS" in rich
    assert "06/10/2026" in rich


def test_notification_delivered_has_no_eta():
    shipment = SimpleNamespace(
        tracking_number="AP499229999BR",
        carrier_name="Correios",
        extra_json=json.dumps(
            {
                "estimatedDelivery": "2026-10-06",
                "trackingEvents": [
                    {
                        "createdAt": "2026-09-22 14:32:00",
                        "description": (
                            "Objeto entregue ao destinatário"
                        ),
                        "from": (
                            "Unidade de Distribuição - "
                            "IVINHEMA/MS"
                        ),
                        "to": "Destinatário",
                    }
                ],
            }
        ),
    )
    event = SimpleNamespace(
        status="delivered",
        event_at="2026-09-22T14:32:00-03:00",
        description="Objeto entregue ao destinatário",
        location="IVINHEMA/MS",
    )

    rich = format_tracking_notification_rich_html(
        _sub(),
        shipment,
        event,
        "America/Sao_Paulo",
    )

    assert "✅ Entregue" in rich
    assert "22 Set. - 14:32" in rich
    assert "Objeto entregue ao destinatário" in rich
    assert "IVINHEMA/MS" in rich
    assert "Entrega concluída com sucesso" in rich
    assert "Previsão de entrega" not in rich
