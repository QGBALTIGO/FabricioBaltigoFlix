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

    async def query_provider(
        provider,
        shipment,
    ):
        if provider.name == "correios_direct":
            return fresh
        return stale

    monkeypatch.setattr(
        service,
        "_query_provider",
        query_provider,
    )

    class ScalarRows:
        def all(self):
            return []

    class FakeSession:
        async def scalars(self, statement):
            return ScalarRows()

        def add(self, value):
            return None

        async def flush(self):
            return None

        async def commit(self):
            return None

    result = await service._query_best_provider(
        FakeSession(),
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
            __import__("datetime").timedelta(
                hours=-3
            )
        ),
    )



def test_freshness_prefers_rastreador_pacotes_on_same_time():
    rastreador = _tracking(
        "rastreador_pacotes",
        "2026-09-21T08:49:19-03:00",
    )
    proxy = _tracking(
        "correios_direct",
        "2026-09-21T08:49:19-03:00",
    )

    assert (
        TrackingService._provider_freshness_key(rastreador)
        > TrackingService._provider_freshness_key(proxy)
    )
