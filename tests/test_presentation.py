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
        last_description="Objeto em transferência para a próxima unidade.",
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



def test_premium_card_has_clear_information_hierarchy(monkeypatch):
    monkeypatch.setattr(
        "app.presentation.humanize_age",
        lambda value: "há 12 min",
    )

    text = format_tracking_card(
        SimpleNamespace(
            nickname="Placa 10k"
        ),
        _shipment(),
        "America/Campo_Grande",
    )

    positions = [
        text.index("📦 Placa 10k"),
        text.index("🚚 Em trânsito"),
        text.index("🕐 <b>Última atualização</b>"),
        text.index("📍 <b>Rota atual</b>"),
        text.index("📅 <b>Previsão de entrega</b>"),
        text.index("🕒 <i>Atualizado há 12 min</i>"),
    ]

    assert positions == sorted(positions)
    assert "Objeto em transferência para a próxima unidade." in text
    assert "💡" not in text
    assert "Agora:" not in text


def test_premium_rich_card_uses_same_hierarchy(monkeypatch):
    monkeypatch.setattr(
        "app.presentation.humanize_age",
        lambda value: "há 8 min",
    )

    rich = format_tracking_rich_html(
        SimpleNamespace(
            nickname="Placa 10k"
        ),
        _shipment(),
        "America/Campo_Grande",
    )

    assert "<b>📦 Placa 10k</b>" in rich
    assert "<b>🚚 Em trânsito</b>" in rich
    assert "🕐 <b>Última atualização</b>" in rich
    assert "📍 <b>Rota atual</b>" in rich
    assert "📅 <b>Previsão de entrega</b>" in rich
    assert "🕒 <i>Atualizado há 8 min</i>" in rich
    assert "💡" not in rich


def test_delivered_card_hides_delivery_forecast(monkeypatch):
    shipment = _shipment()
    shipment.status = "delivered"
    shipment.last_description = "Objeto entregue ao destinatário."

    monkeypatch.setattr(
        "app.presentation.humanize_age",
        lambda value: "há 3 dias",
    )

    rich = format_tracking_rich_html(
        SimpleNamespace(
            nickname="Pedido entregue"
        ),
        shipment,
        "America/Campo_Grande",
    )

    assert "✅ Entregue" in rich
    assert "Objeto entregue ao destinatário." in rich
    assert "Atualizado há 3 dias" in rich
    assert "Previsão de entrega" not in rich
