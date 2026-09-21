from __future__ import annotations

import re
from typing import Any

import httpx

from app.http_client import get_provider_http_client

from app.providers.base import (
    ProviderEvent,
    ProviderNotFound,
    ProviderTracking,
    ProviderUnavailable,
)
from app.status import normalize_status
from app.utils import parse_datetime_assuming_timezone


class RastreadorPacotesProvider:
    name = "rastreador_pacotes"
    endpoint = "https://api.rastreadordepacotes.com.br/rastreio"

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout

    @staticmethod
    def can_handle(number: str) -> bool:
        return bool(
            re.fullmatch(
                r"[A-Z]{2}\d{9}BR",
                number.strip().upper(),
            )
        )

    async def register(
        self,
        tracking_number: str,
    ) -> ProviderTracking:
        return await self.fetch(tracking_number)

    @staticmethod
    def _normalize_city_uf(value: str) -> str:
        text = re.sub(r"\s*/\s*", "/", value.strip())
        return re.sub(r"\s+", " ", text).strip()

    @classmethod
    def _route_parts(
        cls,
        details: str,
    ) -> tuple[str | None, str | None]:
        text = (
            details.replace("\r", " ")
            .replace("\n", " ")
            .strip()
        )
        text = re.sub(r"\s+", " ", text)

        marker = re.search(
            r"\bSaiu de (.+?) para (.+)$",
            text,
            flags=re.IGNORECASE,
        )
        if not marker:
            return None, None

        def normalize_place(place: str) -> str:
            place = cls._normalize_city_uf(place)
            place = re.sub(
                r"\s+em\s+",
                " - ",
                place,
                count=1,
                flags=re.IGNORECASE,
            )
            return place.strip()

        return (
            normalize_place(marker.group(1)),
            normalize_place(marker.group(2)),
        )

    @classmethod
    def _event_location(
        cls,
        position: dict[str, Any],
    ) -> str | None:
        details = str(
            position.get("DetalhesFormatado")
            or position.get("Detalhes")
            or ""
        )

        origin, _ = cls._route_parts(details)
        if origin:
            city = re.search(
                r"([A-Za-zÀ-ÿ0-9 .'-]+)/([A-Z]{2})\b",
                origin,
            )
            if city:
                return cls._normalize_city_uf(
                    f"{city.group(1)}/{city.group(2)}"
                )

        local = str(position.get("Local") or "").strip()
        uf = str(position.get("UF") or "").strip()
        if local and uf:
            return cls._normalize_city_uf(f"{local}/{uf}")
        if local:
            return cls._normalize_city_uf(local)
        if uf:
            return uf
        return None

    @classmethod
    def parse_result(
        cls,
        tracking_number: str,
        data: dict[str, Any],
    ) -> ProviderTracking:
        tracking = data.get("tracking") or []
        if isinstance(tracking, dict):
            tracking = [tracking]

        if not tracking:
            raise ProviderNotFound(
                "Código não encontrado no Rastreador de Pacotes."
            )

        item = tracking[0] or {}
        positions = item.get("Posicoes") or []

        if not isinstance(positions, list) or not positions:
            raise ProviderNotFound(
                "Sem movimentações no Rastreador de Pacotes."
            )

        events: list[ProviderEvent] = []

        for position in positions:
            if not isinstance(position, dict):
                continue

            action = str(
                position.get("Acao")
                or "Atualização de rastreio"
            ).strip()

            details = str(
                position.get("Detalhes")
                or ""
            ).strip()

            description = action
            if (
                details
                and details.lower()
                not in action.lower()
            ):
                description = f"{action} — {details}"

            events.append(
                ProviderEvent(
                    status=normalize_status(action, details),
                    status_raw=(
                        str(
                            position.get("StatusPosicao")
                            or action
                        ).strip()
                        or None
                    ),
                    description=description,
                    location=cls._event_location(position),
                    event_at=parse_datetime_assuming_timezone(
                        position.get("Data"),
                        "America/Sao_Paulo",
                    ),
                )
            )

        if not events:
            raise ProviderNotFound(
                "Sem eventos válidos no Rastreador de Pacotes."
            )

        events.sort(key=lambda event: event.event_at)

        return ProviderTracking(
            tracking_number=tracking_number,
            provider=cls.name,
            carrier_code="correios",
            carrier_name="Correios",
            status_raw=events[-1].status_raw,
            events=events,
            raw=data,
        )

    async def fetch(
        self,
        tracking_number: str,
        carrier_code: str | None = None,
        provider_tracking_id: str | None = None,
    ) -> ProviderTracking:
        number = tracking_number.strip().upper()

        if not self.can_handle(number):
            raise ProviderNotFound(
                "Formato não compatível com Correios."
            )

        try:
            client = await get_provider_http_client()
            response = await client.get(
                f"{self.endpoint}/{number}",
                timeout=self.timeout,
                headers={
                        "Accept": "application/json",
                        "User-Agent": (
                            "Mozilla/5.0 "
                            "(Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 "
                            "(KHTML, like Gecko) "
                            "Chrome/124.0.0.0 Safari/537.36"
                        ),
                },
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderUnavailable(
                "Rastreador de Pacotes indisponível."
            ) from exc

        return self.parse_result(number, data)
