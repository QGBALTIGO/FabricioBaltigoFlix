from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(slots=True)
class ProviderEvent:
    status: str
    status_raw: str | None
    description: str
    location: str | None
    event_at: datetime


@dataclass(slots=True)
class ProviderTracking:
    tracking_number: str
    provider: str
    provider_tracking_id: str | None = None
    carrier_code: str | None = None
    carrier_name: str | None = None
    status_raw: str | None = None
    events: list[ProviderEvent] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class TrackingProvider(Protocol):
    name: str

    async def register(self, tracking_number: str) -> ProviderTracking:
        ...

    async def fetch(
        self,
        tracking_number: str,
        carrier_code: str | None = None,
        provider_tracking_id: str | None = None,
    ) -> ProviderTracking:
        ...
