from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from app.http_client import get_provider_http_client

from app.providers.base import (
    ProviderEvent,
    ProviderNotFound,
    ProviderTracking,
    ProviderUnavailable,
)
from app.status import normalize_status


class TotalExpressDirectProvider:
    name = "totalexpress_direct"
    endpoint = (
        "https://totalconecta.totalexpress.com.br/"
        "mfe-rastreio/api/order-data"
    )

    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout

    @staticmethod
    def can_handle(number: str) -> bool:
        normalized = number.upper().strip()
        return bool(
            re.fullmatch(r"BR\d{8,18}XP", normalized)
            or re.fullmatch(r"TE\d{8,20}", normalized)
        )

    async def register(self, tracking_number: str) -> ProviderTracking:
        return await self.fetch(tracking_number)

    async def fetch(
        self,
        tracking_number: str,
        carrier_code: str | None = None,
        provider_tracking_id: str | None = None,
    ) -> ProviderTracking:
        number = tracking_number.upper().strip()
        if not self.can_handle(number):
            raise ProviderNotFound("Formato não compatível com Total Express.")

        try:
            client = await get_provider_http_client()
            response = await client.get(
                self.endpoint,
                params={"awb": number, "language": "pt"},
                timeout=self.timeout,
                headers={
                        "User-Agent": "Mozilla/5.0",
                        "Accept": "application/json, text/plain, */*",
                        "Referer": (
                            "https://totalconecta.totalexpress.com.br/"
                            "mfe-rastreio/"
                        ),
                },
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderUnavailable(
                f"Total Express indisponível: {exc}"
            ) from exc

        layouts = ((data.get("data") or {}).get("layouts")) or []
        events: list[ProviderEvent] = []
        tz = ZoneInfo("America/Sao_Paulo")

        for layout in layouts:
            for etapa in layout.get("etapas") or []:
                for raw in etapa.get("listaStatus") or []:
                    description = str(
                        raw.get("statusDescricao")
                        or "Atualização"
                    ).strip()
                    extra = raw.get("mensagemEvaTraducao") or {}
                    location = (
                        str(extra.get("mensagemEva")).strip()
                        if isinstance(extra, dict)
                        and extra.get("mensagemEva")
                        else None
                    )

                    date = str(raw.get("data") or "")
                    hour = str(raw.get("hora") or "00:00:00")
                    when = datetime.now(tz)
                    if date:
                        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                            try:
                                when = datetime.strptime(
                                    f"{date} {hour}",
                                    fmt,
                                ).replace(tzinfo=tz)
                                break
                            except ValueError:
                                continue

                    events.append(
                        ProviderEvent(
                            status=normalize_status(description, location),
                            status_raw=description,
                            description=description,
                            location=location,
                            event_at=when,
                        )
                    )

        if not events:
            raise ProviderNotFound("Sem eventos da Total Express.")

        events.sort(key=lambda item: item.event_at)

        return ProviderTracking(
            tracking_number=number,
            provider=self.name,
            carrier_code="totalexpress",
            carrier_name="Total Express",
            status_raw=events[-1].status_raw,
            events=events,
            raw=data,
        )
