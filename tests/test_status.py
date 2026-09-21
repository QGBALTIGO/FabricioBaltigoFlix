from app.status import normalize_status, should_notify


def test_normalize_portuguese_statuses():
    assert normalize_status(
        "",
        "Objeto saiu para entrega ao destinatário",
    ) == "out_for_delivery"
    assert normalize_status(
        "",
        "Objeto entregue ao destinatário",
    ) == "delivered"
    assert normalize_status(
        "",
        "Objeto encaminhado",
    ) == "in_transit"


def test_notify_levels():
    assert should_notify(
        "all",
        "in_transit",
    ) is True
    assert should_notify(
        "important",
        "out_for_delivery",
    ) is True
    assert should_notify(
        "important",
        "in_transit",
    ) is False
    assert should_notify(
        "off",
        "delivered",
    ) is False


def test_normalize_provider_statuses():
    assert normalize_status(
        "InfoReceived"
    ) == "info_received"
    assert normalize_status(
        "InTransit"
    ) == "in_transit"
    assert normalize_status(
        "OutForDelivery"
    ) == "out_for_delivery"
    assert normalize_status(
        "DeliveryFailure"
    ) == "delivery_failed"
