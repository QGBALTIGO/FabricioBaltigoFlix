import httpx
import pytest

from app.config import Settings
from app.providers.base import (
    ProviderNotFound,
    ProviderUnavailable,
)
from app.providers.melhor_rastreio import (
    MelhorRastreioProvider,
)
from app.services.tracking import TrackingService


class _FakeClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.timeouts = []

    async def post(
        self,
        url,
        *,
        json,
        timeout,
        headers,
    ):
        self.timeouts.append(
            float(timeout)
        )

        outcome = self.outcomes.pop(0)
        if isinstance(
            outcome,
            Exception,
        ):
            raise outcome

        return httpx.Response(
            200,
            json=outcome,
            request=httpx.Request(
                "POST",
                url,
            ),
        )


@pytest.mark.asyncio
async def test_graphql_error_followed_by_healthy_not_found_is_not_provider_failure(
    monkeypatch,
):
    provider = MelhorRastreioProvider(
        timeout=30,
        total_timeout=10,
    )
    monkeypatch.setattr(
        provider,
        "_candidate_types",
        lambda number, carrier_code=None: [
            "correios"
        ],
    )

    client = _FakeClient(
        [
            {
                "errors": [
                    {
                        "message": "temporary resolver error"
                    }
                ]
            },
            {
                "data": {
                    "result": None
                }
            },
        ]
    )
    monkeypatch.setattr(
        "app.providers.melhor_rastreio.get_provider_http_client",
        lambda: _async_value(
            client
        ),
    )

    with pytest.raises(
        ProviderNotFound
    ):
        await provider.fetch(
            "AB123456789BR"
        )

    assert len(
        client.timeouts
    ) == 2
    assert all(
        timeout <= 4.0
        for timeout in client.timeouts
    )


@pytest.mark.asyncio
async def test_all_transport_failures_remain_provider_unavailable(
    monkeypatch,
):
    provider = MelhorRastreioProvider(
        timeout=30,
        total_timeout=10,
    )
    monkeypatch.setattr(
        provider,
        "_candidate_types",
        lambda number, carrier_code=None: [
            "correios"
        ],
    )

    client = _FakeClient(
        [
            httpx.ConnectError(
                "endpoint 1 down"
            ),
            httpx.ConnectError(
                "endpoint 2 down"
            ),
        ]
    )
    monkeypatch.setattr(
        "app.providers.melhor_rastreio.get_provider_http_client",
        lambda: _async_value(
            client
        ),
    )

    with pytest.raises(
        ProviderUnavailable
    ):
        await provider.fetch(
            "AB123456789BR"
        )


def test_tracking_service_gives_melhor_provider_smaller_budget_than_outer_timeout():
    settings = Settings(
        melhor_rastreio_enabled=True,
        direct_fallbacks_enabled=False,
        http_timeout_seconds=30,
        provider_query_timeout_seconds=12,
    )

    service = TrackingService(
        settings
    )

    assert service.melhor is not None
    assert (
        service.melhor.total_timeout
        < settings.provider_query_timeout_seconds
    )
    assert (
        service.melhor.total_timeout
        == 11.5
    )


async def _async_value(
    value,
):
    return value
