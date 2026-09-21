from app.providers.melhor_rastreio import MelhorRastreioProvider


def test_candidate_types():
    assert MelhorRastreioProvider._candidate_types("AA123456789BR")[0] == "correios"
    assert MelhorRastreioProvider._candidate_types("ME262D64YI0BR")[0] == "melhorenvio"
    assert MelhorRastreioProvider._candidate_types("12345678901234")[0] == "jadlog"


def test_parse_linked_tracker_result():
    result = {
        "id": "parcel-1",
        "lastStatus": "DELIVERED",
        "estimatedDelivery": "2026-09-25T23:59:59-03:00",
        "trackers": [
            {
                "type": "melhorenvio",
                "shippingService": None,
                "trackingCode": "ME262D64YI0BR",
            },
            {
                "type": "jadlog",
                "shippingService": None,
                "trackingCode": "12345678901234",
            },
        ],
        "trackingEvents": [
            {
                "createdAt": "2026-09-20T10:00:00-03:00",
                "title": "Objeto em trânsito",
                "description": "Objeto encaminhado",
                "from": "01 - CAMPO GRANDE/MS",
                "to": "01 - IVINHEMA/MS",
                "location": {
                    "city": "Campo Grande",
                    "state": "MS",
                    "country": "BR",
                },
            },
            {
                "createdAt": "2026-09-21T12:00:00-03:00",
                "title": "Objeto entregue",
                "description": "Entrega realizada",
                "from": "01 - IVINHEMA/MS",
                "to": "DESTINATARIO",
                "location": {
                    "city": "Ivinhema",
                    "state": "MS",
                    "country": "BR",
                },
            },
        ],
    }

    parsed = MelhorRastreioProvider.parse_result(
        "ME262D64YI0BR",
        "melhorenvio",
        result,
    )

    assert parsed.provider == "melhor_rastreio"
    assert parsed.carrier_code == "jadlog"
    assert parsed.carrier_name == "Jadlog"
    assert len(parsed.events) == 2
    assert parsed.events[-1].status == "delivered"



def test_melhor_rastreio_naive_time_is_brazil_local():
    result = {
        "id": "parcel-time",
        "lastStatus": "ONROUTE",
        "trackers": [
            {
                "type": "correios",
                "shippingService": None,
                "trackingCode": "AP499229999BR",
            }
        ],
        "trackingEvents": [
            {
                "createdAt": "2026-09-18 16:40:00",
                "title": "Objeto em transferência",
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
    }

    parsed = MelhorRastreioProvider.parse_result(
        "AP499229999BR",
        "correios",
        result,
    )

    assert parsed.events[0].event_at.hour == 16
    assert parsed.events[0].event_at.isoformat().endswith("-03:00")
