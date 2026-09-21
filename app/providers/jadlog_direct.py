from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from app.providers.base import (
    ProviderEvent,
    ProviderNotFound,
    ProviderTracking,
    ProviderUnavailable,
)
from app.status import normalize_status


class JadlogDirectProvider:
    name = "jadlog_direct"
    endpoint = "https://www.jadlog.com.br/jadlog/rastreio"

    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout

    @staticmethod
    def can_handle(number: str) -> bool:
        raw = (number or "").strip()
        if not raw or re.search(r"[A-Za-z]", raw):
            return False
        if not re.fullmatch(r"[\d.\-\s]+", raw):
            return False
        normalized = re.sub(r"\D", "", raw)
        return bool(re.fullmatch(r"\d{1,14}", normalized))

    async def register(self, tracking_number: str) -> ProviderTracking:
        return await self.fetch(tracking_number)

    async def fetch(
        self,
        tracking_number: str,
        carrier_code: str | None = None,
        provider_tracking_id: str | None = None,
    ) -> ProviderTracking:
        number = re.sub(r"\D", "", tracking_number)
        if not self.can_handle(number):
            raise ProviderNotFound("Formato não compatível com Jadlog.")

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self.endpoint,
                    data={"cte": number},
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Accept": "text/html,application/xhtml+xml",
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Origin": "https://www.jadlog.com.br",
                        "Referer": "https://www.jadlog.com.br/jadlog/home",
                    },
                    follow_redirects=True,
                )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(
                f"Consulta pública da Jadlog indisponível: {exc}"
            ) from exc

        html = response.text
        soup = BeautifulSoup(html, "html.parser")
        page_text = soup.get_text(" ", strip=True)

        if "g-recaptcha" in html and "Status referente" not in page_text:
            raise ProviderUnavailable("Jadlog solicitou CAPTCHA.")

        events: list[ProviderEvent] = []
        tz = ZoneInfo("America/Sao_Paulo")

        for status_node in soup.select("p.txt-status"):
            date_node = status_node.select_one(".txt-data")
            raw_date = (
                re.sub(r"\s+", " ", date_node.get_text(" ", strip=True)).strip()
                if date_node
                else None
            )
            if date_node:
                date_node.extract()

            description = re.sub(
                r"\s+",
                " ",
                status_node.get_text(" ", strip=True),
            ).strip()
            if not description:
                continue

            when = datetime.now(tz)
            if raw_date:
                try:
                    when = datetime.strptime(
                        raw_date,
                        "%d/%m/%Y - %H:%M",
                    ).replace(tzinfo=tz)
                except ValueError:
                    pass

            events.append(
                ProviderEvent(
                    status=normalize_status(description, description),
                    status_raw=description,
                    description=description,
                    location=None,
                    event_at=when,
                )
            )

        if not events:
            raise ProviderNotFound("Sem eventos públicos da Jadlog.")

        events.sort(key=lambda item: item.event_at)

        return ProviderTracking(
            tracking_number=number,
            provider=self.name,
            carrier_code="jadlog",
            carrier_name="Jadlog",
            status_raw=events[-1].status_raw,
            events=events,
            raw={"source": self.endpoint},
        )
