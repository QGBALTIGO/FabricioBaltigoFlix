from __future__ import annotations

import re
from typing import Any

import httpx

from app.providers.base import (
    ProviderEvent,
    ProviderNotFound,
    ProviderTracking,
    ProviderUnavailable,
)
from app.status import normalize_status
from app.utils import parse_datetime


class CorreiosDirectProvider:
    name = "correios_direct"
    endpoint = "https://proxyapp.correios.com.br/v1/sro-rastro"

    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout

    @staticmethod
    def can_handle(number: str) -> bool:
        return bool(re.fullmatch(r"[A-Z]{2}\d{9}BR", number.upper()))

    async def register(self, tracking_number: str) -> ProviderTracking:
        return await self.fetch(tracking_number)

    async def fetch(
        self,
        tracking_number: str,
        carrier_code: str | None = None,
        provider_tracking_id: str | None = None,
    ) -> ProviderTracking:
        number = tracking_number.strip().upper()
        if not self.can_handle(number):
            raise ProviderNotFound("Formato não compatível com Correios.")

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.endpoint}/{number}",
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "Mozilla/5.0",
                        "Origin": "https://rastreamento.correios.com.br",
                        "Referer": "https://rastreamento.correios.com.br/",
                    },
                )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderUnavailable(
                f"Consulta pública dos Correios indisponível: {exc}"
            ) from exc

        objetos = data.get("objetos") or []
        if not objetos:
            raise ProviderNotFound("Objeto não encontrado nos Correios.")

        item = objetos[0] or {}
        raw_events = item.get("eventos") or []
        if not raw_events:
            raise ProviderNotFound("Sem eventos dos Correios.")

        events: list[ProviderEvent] = []
        for raw in raw_events:
            description = str(
                raw.get("descricao")
                or raw.get("descricaoFrontEnd")
                or raw.get("descricaoWeb")
                or "Atualização"
            ).strip()

            created = raw.get("dtHrCriado")
            if isinstance(created, dict):
                created = created.get("date")

            unit = raw.get("unidade") or {}
            address = unit.get("endereco") if isinstance(unit, dict) else {}
            if not isinstance(address, dict):
                address = {}

            location_parts = [
                str(address.get("cidade")).strip()
                if address.get("cidade")
                else "",
                str(address.get("uf")).strip()
                if address.get("uf")
                else "",
            ]
            location = "/".join(
                part for part in location_parts if part
            ) or None

            events.append(
                ProviderEvent(
                    status=normalize_status(description, description),
                    status_raw=description,
                    description=description,
                    location=location,
                    event_at=parse_datetime(created),
                )
            )

        events.sort(key=lambda item: item.event_at)

        return ProviderTracking(
            tracking_number=number,
            provider=self.name,
            carrier_code="correios",
            carrier_name="Correios",
            status_raw=events[-1].status_raw if events else None,
            events=events,
            raw=data,
        )
