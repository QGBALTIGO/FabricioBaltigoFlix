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
from app.utils import parse_datetime_assuming_timezone


TRACKER_LABELS = {
    "correios": "Correios",
    "jadlog": "Jadlog",
    "buslog": "Buslog",
    "viamundo": "Viação Mundo",
    "azul": "Azul Cargo",
    "latam": "LATAM Cargo",
    "loggi": "Loggi",
    "jet": "J&T Express",
    "melhorenvio": "Melhor Envio",
    "unknown": "Transportadora",
}


class MelhorRastreioProvider:
    name = "melhor_rastreio"

    endpoints = (
        "https://api.melhorrastreio.com.br/graphql",
        "https://melhor-rastreio-api.melhorrastreio.com.br/graphql",
    )

    query = """
    mutation searchParcel($tracker: TrackerSearchInput!) {
      result: searchParcel(tracker: $tracker) {
        id
        updatedAt
        lastStatus
        estimatedDelivery
        trackers {
          type
          shippingService
          trackingCode
        }
        trackingEvents {
          trackerType
          trackingCode
          createdAt
          title
          description
          from
          to
          location {
            zipcode
            address
            locality
            number
            complement
            city
            state
            country
          }
          additionalInfo
        }
      }
    }
    """

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    async def register(self, tracking_number: str) -> ProviderTracking:
        return await self.fetch(tracking_number)

    async def fetch(
        self,
        tracking_number: str,
        carrier_code: str | None = None,
        provider_tracking_id: str | None = None,
    ) -> ProviderTracking:
        number = tracking_number.strip().upper()
        tracker_types = self._candidate_types(number, carrier_code)
        last_transport_error: Exception | None = None

        for tracker_type in tracker_types:
            payload = {
                "query": self.query,
                "variables": {
                    "tracker": {
                        "trackingCode": number,
                        "type": tracker_type,
                    }
                },
            }

            for endpoint in self.endpoints:
                try:
                    async with httpx.AsyncClient(timeout=self.timeout) as client:
                        response = await client.post(
                            endpoint,
                            json=payload,
                            headers={
                                "Content-Type": "application/json",
                                "Accept": "application/json",
                                "User-Agent": (
                                    "Mozilla/5.0 (compatible; MelhorRastreioBot/1.0)"
                                ),
                                "Origin": "https://melhorrastreio.com.br",
                                "Referer": "https://melhorrastreio.com.br/",
                            },
                        )
                    response.raise_for_status()
                    body = response.json()
                except (httpx.HTTPError, ValueError) as exc:
                    last_transport_error = exc
                    continue

                errors = body.get("errors") or []
                if errors:
                    message = "; ".join(
                        str(item.get("message") or item)
                        for item in errors
                    )
                    if "unauthorized" in message.lower():
                        last_transport_error = ProviderUnavailable(message)
                        continue
                    last_transport_error = ProviderUnavailable(message)
                    continue

                result = (body.get("data") or {}).get("result")
                if not result:
                    break

                return self.parse_result(number, tracker_type, result)

        if last_transport_error:
            raise ProviderUnavailable(
                f"Melhor Rastreio indisponível: {last_transport_error}"
            ) from last_transport_error

        raise ProviderNotFound(
            f"Código {number} ainda não encontrado no Melhor Rastreio."
        )

    @classmethod
    def _candidate_types(
        cls,
        number: str,
        carrier_code: str | None = None,
    ) -> list[str]:
        allowed = {
            "correios",
            "jadlog",
            "buslog",
            "viamundo",
            "azul",
            "latam",
            "loggi",
            "jet",
            "melhorenvio",
            "unknown",
        }

        result: list[str] = []
        if carrier_code and carrier_code in allowed:
            result.append(carrier_code)

        if re.fullmatch(r"[A-Z]{2}\d{9}BR", number):
            result.extend(["correios", "melhorenvio"])
        elif number.startswith(("ME", "LTM-")):
            result.extend(["melhorenvio", "jadlog"])
        elif number.startswith("LGI"):
            result.extend(["loggi", "melhorenvio"])
        elif number.isdigit():
            result.extend(["jadlog", "jet", "azul", "latam", "buslog"])
        else:
            result.extend(
                [
                    "melhorenvio",
                    "loggi",
                    "jet",
                    "jadlog",
                    "correios",
                    "azul",
                    "latam",
                    "buslog",
                    "viamundo",
                    "unknown",
                ]
            )

        deduped: list[str] = []
        for item in result:
            if item in allowed and item not in deduped:
                deduped.append(item)
        return deduped

    @classmethod
    def parse_result(
        cls,
        tracking_number: str,
        requested_type: str,
        result: dict[str, Any],
    ) -> ProviderTracking:
        trackers = result.get("trackers") or []
        if isinstance(trackers, dict):
            trackers = [trackers]

        physical = None
        for tracker in trackers:
            tracker_type = str(tracker.get("type") or "")
            if tracker_type and tracker_type != "melhorenvio":
                physical = tracker
                break

        if physical is None and trackers:
            physical = trackers[0]

        carrier_code = str(
            (physical or {}).get("type")
            or requested_type
        )
        carrier_name = (
            TRACKER_LABELS.get(carrier_code)
            or str((physical or {}).get("shippingService") or "")
            or "Transportadora"
        )

        last_status = str(result.get("lastStatus") or "")
        raw_events = result.get("trackingEvents") or []
        if isinstance(raw_events, dict):
            raw_events = [raw_events]

        events: list[ProviderEvent] = []
        for raw in raw_events:
            title = str(raw.get("title") or "").strip()
            description = str(raw.get("description") or "").strip()
            text = title or description or "Atualização de rastreio"
            if description and description.lower() not in text.lower():
                text = f"{text} — {description}"

            location_obj = raw.get("location") or {}
            location = None
            if isinstance(location_obj, dict):
                parts = [
                    str(location_obj.get(key)).strip()
                    for key in ("city", "state", "country")
                    if location_obj.get(key)
                ]
                location = ", ".join(parts) or None

            status = normalize_status(title, description)
            event_at = parse_datetime_assuming_timezone(
                raw.get("createdAt"),
                "America/Sao_Paulo",
            )

            events.append(
                ProviderEvent(
                    status=status,
                    status_raw=title or None,
                    description=text,
                    location=location,
                    event_at=event_at,
                )
            )

        events.sort(key=lambda item: item.event_at)

        if events and events[-1].status == "unknown" and last_status:
            events[-1].status = normalize_status(last_status, events[-1].description)
            events[-1].status_raw = last_status

        return ProviderTracking(
            tracking_number=tracking_number,
            provider=cls.name,
            provider_tracking_id=str(result.get("id") or "") or None,
            carrier_code=carrier_code,
            carrier_name=carrier_name,
            status_raw=last_status or None,
            events=events,
            raw=result,
        )
