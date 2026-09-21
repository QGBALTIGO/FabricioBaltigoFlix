from __future__ import annotations

import re

STATUS_META = {
    "unknown": ("❓", "Aguardando informações"),
    "info_received": ("📝", "Envio registrado"),
    "picked_up": ("📦", "Coletado"),
    "in_transit": ("🚚", "Em trânsito"),
    "customs": ("🛃", "Fiscalização/alfândega"),
    "arrived_destination": ("📍", "Chegou ao destino"),
    "out_for_delivery": ("🛵", "Saiu para entrega"),
    "available_for_pickup": ("🏤", "Disponível para retirada"),
    "delivery_failed": ("⚠️", "Tentativa de entrega"),
    "exception": ("❗", "Problema no envio"),
    "returned": ("↩️", "Devolução"),
    "delivered": ("✅", "Entregue"),
}

IMPORTANT_STATUSES = {
    "info_received",
    "picked_up",
    "customs",
    "arrived_destination",
    "out_for_delivery",
    "available_for_pickup",
    "delivery_failed",
    "exception",
    "returned",
    "delivered",
}

DIRECT_ALIASES = {
    "notfound": "unknown",
    "inforeceived": "info_received",
    "pickedup": "picked_up",
    "intransit": "in_transit",
    "departure": "in_transit",
    "arrival": "arrived_destination",
    "availableforpickup": "available_for_pickup",
    "outfordelivery": "out_for_delivery",
    "deliveryfailure": "delivery_failed",
    "delivered": "delivered",
    "exception": "exception",
    "expired": "exception",
    "returned": "returned",
    "returning": "returned",
    "customs": "customs",
}


def status_label(status: str) -> str:
    emoji, label = STATUS_META.get(
        status,
        STATUS_META["unknown"],
    )
    return f"{emoji} {label}"


def normalize_status(
    raw: str | None,
    description: str | None = None,
) -> str:
    raw_text = str(raw or "").strip()
    key = re.sub(
        r"[^a-z0-9]",
        "",
        raw_text.lower(),
    )

    if key in DIRECT_ALIASES:
        return DIRECT_ALIASES[key]

    for alias, normalized in DIRECT_ALIASES.items():
        if alias and alias in key:
            return normalized

    text = f"{raw_text} {description or ''}".lower().strip()
    compact = re.sub(
        r"[_\-]+",
        " ",
        text,
    )

    rules = [
        (
            "delivered",
            [
                "delivered",
                "entregue",
                "delivery successful",
            ],
        ),
        (
            "out_for_delivery",
            [
                "out for delivery",
                "saiu para entrega",
                "em rota de entrega",
                "delivery today",
            ],
        ),
        (
            "available_for_pickup",
            [
                "available for pickup",
                "aguardando retirada",
                "disponível para retirada",
            ],
        ),
        (
            "delivery_failed",
            [
                "delivery failed",
                "tentativa de entrega",
                "destinatário ausente",
                "recipient absent",
            ],
        ),
        (
            "returned",
            [
                "returned",
                "returning",
                "devolvido",
                "devolução",
                "retorno ao remetente",
            ],
        ),
        (
            "customs",
            [
                "customs",
                "alfândega",
                "aduaneira",
                "fiscalização",
            ],
        ),
        (
            "exception",
            [
                "exception",
                "problem",
                "problema",
                "falha",
                "damaged",
                "lost",
                "extraviado",
                "expired",
            ],
        ),
        (
            "picked_up",
            [
                "picked up",
                "collected",
                "coletado",
                "postado",
                "posted",
            ],
        ),
        (
            "arrived_destination",
            [
                "arrived at destination",
                "cidade de destino",
                "destination facility",
            ],
        ),
        (
            "in_transit",
            [
                "in transit",
                "transit",
                "transporting",
                "encaminhado",
                "transferência",
            ],
        ),
        (
            "info_received",
            [
                "info received",
                "information received",
                "label created",
                "pré-postagem",
                "pre postagem",
                "registrado",
            ],
        ),
    ]

    for normalized, needles in rules:
        if any(n in compact for n in needles):
            return normalized

    return "unknown"


def should_notify(
    notify_level: str,
    status: str,
) -> bool:
    if notify_level == "all":
        return True
    if notify_level == "off":
        return False
    return status in IMPORTANT_STATUSES
