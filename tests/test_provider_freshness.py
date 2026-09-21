from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.providers.base import (
    ProviderEvent,
    ProviderTracking,
)
from app.services.tracking import TrackingService


def _tracking(provider: str, when: str) -> ProviderTracking:
    dt = datetime.fromisoformat(when)
    return ProviderTracking(
        tracking_number="AP499229999BR",
        provider=provider,
        carrier_code="correios",
        carrier_name="Correios",
        events=[
            ProviderEvent(
                status="in_transit",
                status_raw="Em trânsito",
                description=f"Evento de {provider}",
                location="BR",
                event_at=dt,
            )
        ],
    )


def test_provider_freshness_prefers_newer_event():
    stale = _tracking(
        "melhor_rastreio",
        "2026-09-18T16:40:00-03:00",
    )
    fresh = _tracking(
        "correios_direct",
        "2026-09-21T08:49:00-03:00",
    )

    assert (
        TrackingService._provider_freshness_key(fresh)
        > TrackingService._provider_freshness_key(stale)
    )


@pytest.mark.asyncio
async def test_query_best_provider_compares_all_healthy_sources(
    monkeypatch,
):
    service = TrackingService(
        Settings(
            melhor_rastreio_enabled=False,
            direct_fallbacks_enabled=False,
            tracking_poller_enabled=False,
            provider_query_timeout_seconds=3,
        )
    )

    stale_provider = SimpleNamespace(
        name="melhor_rastreio"
    )
    fresh_provider = SimpleNamespace(
        name="correios_direct"
    )

    stale = _tracking(
        "melhor_rastreio",
        "2026-09-18T16:40:00-03:00",
    )
    fresh = _tracking(
        "correios_direct",
        "2026-09-21T08:49:00-03:00",
    )

    monkeypatch.setattr(
        service,
        "_candidate_providers",
        lambda shipment: [
            stale_provider,
            fresh_provider,
        ],
    )

    async def not_quarantined(
        session,
        provider_name,
    ):
        return False

    async def query_provider(
        provider,
        shipment,
    ):
        if provider.name == "correios_direct":
            return fresh
        return stale

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(
        service,
        "_provider_quarantined",
        not_quarantined,
    )
    monkeypatch.setattr(
        service,
        "_query_provider",
        query_provider,
    )
    monkeypatch.setattr(
        service,
        "_record_provider_success",
        noop,
    )
    monkeypatch.setattr(
        service,
        "_record_provider_failure",
        noop,
    )

    result = await service._query_best_provider(
        object(),
        SimpleNamespace(
            tracking_number="AP499229999BR",
            carrier_code="correios",
            provider=None,
            provider_tracking_id=None,
        ),
    )

    assert result is fresh
    assert result.events[0].event_at == datetime(
        2026,
        9,
        21,
        8,
        49,
        tzinfo=timezone(
            # -03:00
            __import__("datetime").timedelta(
                hours=-3
            )
        ),
    )
