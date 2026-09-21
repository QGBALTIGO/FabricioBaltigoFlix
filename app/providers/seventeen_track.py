from __future__ import annotations

from typing import Any

import httpx

from app.providers.base import ProviderEvent, ProviderTracking
from app.status import normalize_status
from app.utils import parse_datetime


class SeventeenTrackError(RuntimeError):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


class SeventeenTrackProvider:
    name = "17track"
    base_url = "https://api.17track.net/track/v2.4"

    def __init__(self, token: str, timeout: float = 30):
        self.token = token
        self.timeout = timeout

    @property
    def headers(self) -> dict[str, str]:
        return {"17token": self.token, "Content-Type": "application/json"}

    async def _post(self, path: str, payload: Any) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/{path}",
                headers=self.headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        if data.get("code") not in (0, "0", None):
            raise SeventeenTrackError(
                data.get("message") or "Erro na API 17TRACK",
                data.get("code"),
            )
        return data

    async def register(self, tracking_number: str) -> ProviderTracking:
        data = await self._post(
            "register",
            [{"number": tracking_number, "lang": "pt"}],
        )
        accepted = (data.get("data") or {}).get("accepted") or []
        if not accepted:
            rejected = (data.get("data") or {}).get("rejected") or []
            error = (rejected[0].get("error") if rejected else {}) or {}
            raise SeventeenTrackError(
                error.get("message") or "Código rejeitado pela 17TRACK",
                error.get("code"),
            )
        item = accepted[0]
        carrier = item.get("carrier")
        return ProviderTracking(
            tracking_number=tracking_number,
            provider=self.name,
            carrier_code=str(carrier) if carrier else None,
            status_raw="InfoReceived",
            raw=item,
        )

    async def fetch(
        self,
        tracking_number: str,
        carrier_code: str | None = None,
        provider_tracking_id: str | None = None,
    ) -> ProviderTracking:
        item: dict[str, Any] = {"number": tracking_number}
        if carrier_code and str(carrier_code).isdigit():
            item["carrier"] = int(carrier_code)
        data = await self._post("gettrackinfo", [item])
        accepted = (data.get("data") or {}).get("accepted") or []
        if not accepted:
            rejected = (data.get("data") or {}).get("rejected") or []
            if rejected:
                error = rejected[0].get("error") or {}
                raise SeventeenTrackError(
                    error.get("message") or "Sem informações de rastreio.",
                    error.get("code"),
                )
            return ProviderTracking(
                tracking_number=tracking_number,
                provider=self.name,
                carrier_code=carrier_code,
                raw=data,
            )
        return self.parse_item(accepted[0])

    @classmethod
    def parse_webhook(cls, payload: dict[str, Any]) -> list[ProviderTracking]:
        data = payload.get("data")
        if not data:
            return []
        if isinstance(data, dict) and data.get("number"):
            return [cls.parse_item(data)]
        items = (
            data.get("accepted") or data.get("trackings") or []
            if isinstance(data, dict)
            else data
        )
        if isinstance(items, dict):
            items = [items]
        return [
            cls.parse_item(item)
            for item in (items or [])
            if isinstance(item, dict)
        ]

    @classmethod
    def parse_item(cls, item: dict[str, Any]) -> ProviderTracking:
        number = str(
            item.get("number") or item.get("tracking_number") or ""
        ).upper()
        carrier = item.get("carrier")
        track_info = item.get("track_info") or item.get("trackInfo") or {}

        latest_status_obj = (
            track_info.get("latest_status")
            or item.get("latest_status")
            or item.get("status")
        )
        if isinstance(latest_status_obj, dict):
            latest_status = (
                latest_status_obj.get("status")
                or latest_status_obj.get("sub_status")
            )
        else:
            latest_status = latest_status_obj

        tracking_obj = track_info.get("tracking") or {}
        provider_entries = (
            tracking_obj.get("providers")
            if isinstance(tracking_obj, dict)
            else []
        )
        if not provider_entries:
            provider_entries = []
        if isinstance(provider_entries, dict):
            provider_entries = [provider_entries]

        carrier_name = (
            item.get("carrier_name")
            or item.get("carrierName")
        )
        if not carrier_name:
            for entry in provider_entries:
                info = entry.get("provider") or {}
                if str(info.get("key") or "") == str(carrier or ""):
                    carrier_name = info.get("name")
                    break
            if not carrier_name and provider_entries:
                carrier_name = (
                    provider_entries[0].get("provider") or {}
                ).get("name")

        raw_events: list[dict[str, Any]] = []
        for provider in provider_entries:
            nodes = provider.get("events") or []
            if isinstance(nodes, dict):
                nodes = [nodes]
            raw_events.extend(
                x for x in nodes if isinstance(x, dict)
            )

        if not raw_events:
            latest_event = track_info.get("latest_event")
            if isinstance(latest_event, dict):
                raw_events.append(latest_event)

        direct = item.get("events") or track_info.get("events") or []
        if isinstance(direct, dict):
            direct = [direct]
        raw_events.extend(
            x for x in direct if isinstance(x, dict)
        )

        events: list[ProviderEvent] = []
        seen: set[tuple] = set()

        for ev in raw_events:
            translation = ev.get("description_translation")
            translated = (
                translation.get("description")
                if isinstance(translation, dict)
                else None
            )
            description = str(
                translated
                or ev.get("description")
                or ev.get("track_description")
                or ev.get("status_description")
                or ev.get("content")
                or ev.get("message")
                or latest_status
                or "Atualização de rastreio"
            ).strip()

            status_raw = str(
                ev.get("stage")
                or ev.get("sub_status")
                or ev.get("status")
                or latest_status
                or ""
            ).strip() or None

            location = (
                ev.get("location")
                or ev.get("track_location")
            )
            if not location and isinstance(ev.get("address"), dict):
                addr = ev["address"]
                location = ", ".join(
                    str(addr[k])
                    for k in ("city", "state", "country")
                    if addr.get(k)
                ) or None

            when = (
                ev.get("time_iso")
                or ev.get("time_utc")
                or ev.get("time")
                or ev.get("datetime")
                or ev.get("event_time")
                or ev.get("date")
            )
            event_at = parse_datetime(when)

            dedupe_key = (
                status_raw,
                description,
                str(location or ""),
                event_at.isoformat(),
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            events.append(
                ProviderEvent(
                    status=normalize_status(status_raw, description),
                    status_raw=status_raw,
                    description=description,
                    location=str(location).strip() if location else None,
                    event_at=event_at,
                )
            )

        events.sort(key=lambda e: e.event_at)

        return ProviderTracking(
            tracking_number=number,
            provider=cls.name,
            carrier_code=str(carrier) if carrier else None,
            carrier_name=str(carrier_name) if carrier_name else None,
            status_raw=(
                str(latest_status)
                if latest_status is not None
                else None
            ),
            events=events,
            raw=item,
        )
