from __future__ import annotations

from typing import Any

import httpx

from app.http_client import get_provider_http_client

from app.providers.base import ProviderEvent, ProviderTracking
from app.status import normalize_status
from app.utils import parse_datetime


class Ship24Error(RuntimeError):
    pass


class Ship24Provider:
    name = "ship24"
    base_url = "https://api.ship24.com/public/v1"

    def __init__(self, api_key: str, timeout: float = 30):
        self.api_key = api_key
        self.timeout = max(timeout, 65)

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        json: Any = None,
    ) -> dict[str, Any]:
        client = await get_provider_http_client()
        response = await client.request(
            method,
            f"{self.base_url}{path}",
            headers=self.headers,
            json=json,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    async def register(self, tracking_number: str) -> ProviderTracking:
        payload = {"trackingNumber": tracking_number}
        data = await self._request(
            "POST",
            "/trackers/track",
            json=payload,
        )
        return self.parse_payload(data, tracking_number)

    async def fetch(
        self,
        tracking_number: str,
        carrier_code: str | None = None,
        provider_tracking_id: str | None = None,
    ) -> ProviderTracking:
        if provider_tracking_id:
            data = await self._request(
                "GET",
                f"/trackers/{provider_tracking_id}/results",
            )
        else:
            data = await self._request(
                "GET",
                f"/trackers/search/{tracking_number}/results",
            )
        return self.parse_payload(data, tracking_number)

    async def search_carriers(self, query: str) -> list[dict[str, Any]]:
        data = await self._request("GET", "/couriers")
        couriers = (
            (data.get("data") or {}).get("couriers")
            or data.get("couriers")
            or []
        )
        q = query.lower().strip()
        return [
            c
            for c in couriers
            if q in str(
                c.get("courierName") or c.get("name") or ""
            ).lower()
            or q in str(
                c.get("courierCode") or c.get("code") or ""
            ).lower()
        ][:20]

    @classmethod
    def parse_webhook(
        cls,
        payload: dict[str, Any],
    ) -> ProviderTracking:
        number = (
            payload.get("trackingNumber")
            or (payload.get("tracker") or {}).get("trackingNumber")
            or (payload.get("data") or {}).get("trackingNumber")
            or ""
        )
        return cls.parse_payload(payload, str(number))

    @classmethod
    def parse_payload(
        cls,
        data: dict[str, Any],
        tracking_number: str,
    ) -> ProviderTracking:
        body = data.get("data") or data
        tracker = body.get("tracker") or {}
        tracking = (
            body.get("tracking")
            or body.get("trackingResults")
            or body.get("trackings")
            or body
        )
        shipment = (
            tracking.get("shipment")
            if isinstance(tracking, dict)
            else {}
        ) or {}

        if isinstance(tracking, dict):
            tracker_id = (
                tracker.get("trackerId")
                or body.get("trackerId")
                or tracking.get("trackerId")
            )
        else:
            tracker_id = (
                tracker.get("trackerId")
                or body.get("trackerId")
            )

        number = (
            tracker.get("trackingNumber")
            or body.get("trackingNumber")
            or (
                tracking.get("trackingNumber")
                if isinstance(tracking, dict)
                else None
            )
            or tracking_number
        )

        status_raw = (
            shipment.get("statusCode")
            or shipment.get("statusCategory")
            or body.get("statusCode")
        )

        couriers = (
            shipment.get("couriers")
            or body.get("couriers")
            or []
        )
        if isinstance(couriers, dict):
            couriers = [couriers]
        courier = couriers[-1] if couriers else {}

        carrier_code = (
            courier.get("courierCode")
            or courier.get("code")
        )
        carrier_name = (
            courier.get("courierName")
            or courier.get("name")
        )

        raw_events = (
            shipment.get("events")
            or body.get("events")
            or []
        )
        if isinstance(raw_events, dict):
            raw_events = [raw_events]

        events: list[ProviderEvent] = []
        for ev in raw_events:
            description = str(
                ev.get("status")
                or ev.get("statusCode")
                or ev.get("description")
                or ev.get("statusCategory")
                or "Atualização de rastreio"
            )
            raw = str(
                ev.get("statusCode")
                or ev.get("statusCategory")
                or ev.get("status")
                or status_raw
                or ""
            )

            location_obj = ev.get("location")
            if isinstance(location_obj, dict):
                location = ", ".join(
                    str(location_obj.get(k))
                    for k in ("city", "state", "countryCode")
                    if location_obj.get(k)
                ) or None
            else:
                location = (
                    str(location_obj)
                    if location_obj
                    else None
                )

            when = (
                ev.get("occurrenceDatetime")
                or ev.get("datetime")
                or ev.get("date")
            )

            events.append(
                ProviderEvent(
                    status=normalize_status(raw, description),
                    status_raw=raw or None,
                    description=description,
                    location=location,
                    event_at=parse_datetime(when),
                )
            )

        events.sort(key=lambda e: e.event_at)

        return ProviderTracking(
            tracking_number=str(number).upper(),
            provider=cls.name,
            provider_tracking_id=(
                str(tracker_id)
                if tracker_id
                else None
            ),
            carrier_code=(
                str(carrier_code)
                if carrier_code
                else None
            ),
            carrier_name=(
                str(carrier_name)
                if carrier_name
                else None
            ),
            status_raw=(
                str(status_raw)
                if status_raw
                else None
            ),
            events=events,
            raw=data,
        )
