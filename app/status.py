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

STATUS_EXPLANATIONS = {
    "unknown": "Ainda não recebemos uma movimentação suficiente para classificar o envio. Códigos recém-criados podem levar algumas horas para aparecer.",
    "info_received": "A transportadora recebeu os dados do envio, mas a encomenda ainda pode estar aguardando coleta.",
    "picked_up": "A encomenda foi postada ou coletada e entrou na operação da transportadora.",
    "in_transit": "A encomenda está se deslocando entre unidades, centros de distribuição ou cidades. Nem toda etapa gera uma nova leitura.",
    "customs": "A encomenda está em análise aduaneira ou fiscal. Confirme qualquer cobrança somente em canais oficiais.",
    "arrived_destination": "A encomenda chegou à região ou unidade de destino e deve seguir para a etapa local de entrega.",
    "out_for_delivery": "A encomenda saiu com a equipe de entrega. É recomendável ter alguém disponível para recebê-la.",
    "available_for_pickup": "A encomenda está disponível para retirada em um ponto, agência ou unidade indicada pela transportadora.",
    "delivery_failed": "Houve uma tentativa de entrega sem conclusão. Consulte os detalhes para saber se haverá nova tentativa ou retirada.",
    "exception": "A transportadora registrou uma ocorrência que pode exigir atenção, como endereço, fiscalização, avaria ou outro impedimento.",
    "returned": "A encomenda iniciou processo de devolução ao remetente ou já foi devolvida.",
    "delivered": "A transportadora registrou a entrega como concluída.",
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
    emoji, label = STATUS_META.get(status, STATUS_META["unknown"])
    return f"{emoji} {label}"


def status_explanation(status: str) -> str:
    return STATUS_EXPLANATIONS.get(status, STATUS_EXPLANATIONS["unknown"])


def normalize_status(raw: str | None, description: str | None = None) -> str:
    raw_text = str(raw or "").strip()
    key = re.sub(r"[^a-z0-9]", "", raw_text.lower())

    if key in DIRECT_ALIASES:
        return DIRECT_ALIASES[key]

    for alias, normalized in DIRECT_ALIASES.items():
        if alias and alias in key:
            return normalized

    text = f"{raw_text} {description or ''}".lower().strip()
    compact = re.sub(r"[_\-]+", " ", text)

    rules = [
        ("delivered", ["delivered", "entregue", "delivery successful"]),
        ("out_for_delivery", ["out for delivery", "saiu para entrega", "em rota de entrega", "delivery today"]),
        ("available_for_pickup", ["available for pickup", "aguardando retirada", "disponível para retirada"]),
        ("delivery_failed", ["delivery failed", "tentativa de entrega", "destinatário ausente", "recipient absent"]),
        ("returned", ["returned", "returning", "devolvido", "devolução", "retorno ao remetente"]),
        ("customs", ["customs", "alfândega", "aduaneira", "fiscalização", "retenção fiscal", "apreensão fiscal"]),
        ("exception", ["exception", "problem", "problema", "falha", "damaged", "lost", "extraviado", "expired"]),
        ("picked_up", ["picked up", "collected", "coletado", "postado", "posted"]),
        ("arrived_destination", ["arrived at destination", "cidade de destino", "destination facility"]),
        ("in_transit", ["in transit", "transit", "transporting", "encaminhado", "transferência"]),
        ("info_received", ["info received", "information received", "label created", "pré-postagem", "pre postagem", "registrado"]),
    ]

    for normalized, needles in rules:
        if any(n in compact for n in needles):
            return normalized

    return "unknown"


def should_notify(notify_level: str, status: str) -> bool:
    if notify_level == "off":
        return False

    # A UI atual tem apenas alerta ligado/desligado.
    # "important" é mantido como alias legado de ligado para
    # assinaturas criadas antes da simplificação da interface.
    if notify_level in {"all", "important"}:
        return True

    return True
