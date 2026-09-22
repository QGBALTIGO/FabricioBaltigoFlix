from datetime import datetime, timedelta, timezone

import asyncio

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

from app.bot.admin_health import render_admin_health
from app.config import Settings
from app.models import (
    Base,
    OperationalEvent,
    PollingState,
    ProviderHealth,
    Shipment,
    Subscription,
    User,
)
from app.providers.base import (
    ProviderEvent,
    ProviderNotFound,
    ProviderTracking,
    ProviderUnavailable,
)
from app.services.telemetry import (
    build_admin_health_snapshot,
    prune_operational_events,
)
from app.services.tracking import (
    TrackingService,
    _provider_error_detail,
)


@pytest.fixture
def fixed_now():
    return datetime(
        2026,
        9,
        21,
        23,
        30,
        tzinfo=timezone.utc,
    )


async def _db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:"
    )
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all
        )
    Session = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )
    return engine, Session


@pytest.mark.asyncio
async def test_admin_health_snapshot_aggregates_real_metrics(
    fixed_now,
):
    engine, Session = await _db()

    async with Session() as session:
        user = User(
            telegram_id=101,
            first_name="Admin",
        )
        session.add(user)
        await session.flush()

        active = Shipment(
            tracking_number="AA123456789BR",
            carrier_name="Correios",
            status="in_transit",
            is_active=True,
        )
        delivered = Shipment(
            tracking_number="BB123456789BR",
            carrier_name="Jadlog",
            status="delivered",
            is_active=False,
            delivered_at=(
                fixed_now
                - timedelta(days=2)
            ),
        )
        session.add_all(
            [active, delivered]
        )
        await session.flush()

        session.add_all(
            [
                Subscription(
                    user_id=user.id,
                    shipment_id=active.id,
                ),
                Subscription(
                    user_id=user.id,
                    shipment_id=delivered.id,
                ),
                PollingState(
                    shipment_id=active.id,
                    last_checked_at=(
                        fixed_now
                        - timedelta(minutes=20)
                    ),
                    next_check_at=(
                        fixed_now
                        - timedelta(minutes=2)
                    ),
                ),
            ]
        )

        def event(
            kind,
            name,
            ok,
            minutes,
            duration=None,
            detail=None,
            context=None,
        ):
            return OperationalEvent(
                kind=kind,
                name=name,
                ok=ok,
                duration_ms=duration,
                detail=detail,
                context_name=context,
                created_at=(
                    fixed_now
                    - timedelta(
                        minutes=minutes
                    )
                ),
            )

        session.add_all(
            [
                event(
                    "provider_query",
                    "correios_direct",
                    True,
                    5,
                    200,
                    context="Correios",
                ),
                event(
                    "provider_query",
                    "correios_direct",
                    True,
                    10,
                    250,
                    context="Correios",
                ),
                event(
                    "provider_query",
                    "correios_direct",
                    True,
                    15,
                    300,
                    context="Correios",
                ),
                event(
                    "provider_query",
                    "correios_direct",
                    False,
                    20,
                    900,
                    "ProviderUnavailable",
                    "Correios",
                ),
                event(
                    "provider_query",
                    "melhor_rastreio",
                    True,
                    8,
                    600,
                    context="Correios",
                ),
                event(
                    "provider_query",
                    "melhor_rastreio",
                    True,
                    12,
                    650,
                    context="Correios",
                ),
                event(
                    "provider_query",
                    "melhor_rastreio",
                    True,
                    18,
                    700,
                    context="Correios",
                ),
                event(
                    "provider_query",
                    "jadlog_direct",
                    True,
                    25,
                    500,
                    context="Jadlog",
                ),
                event(
                    "notification",
                    "telegram",
                    True,
                    30,
                    100,
                ),
                event(
                    "notification",
                    "telegram",
                    True,
                    40,
                    120,
                ),
                event(
                    "notification",
                    "telegram",
                    False,
                    50,
                    300,
                    "TimedOut",
                ),
                event(
                    "image_scan",
                    "barcode",
                    True,
                    60,
                    80,
                ),
                event(
                    "image_scan",
                    "barcode",
                    False,
                    65,
                    90,
                    "no_candidate",
                ),
                event(
                    "image_scan",
                    "barcode",
                    False,
                    70,
                    95,
                    "error",
                ),
                event(
                    "image_scan",
                    "ocr",
                    True,
                    80,
                    900,
                ),
                event(
                    "image_scan",
                    "ocr",
                    False,
                    90,
                    1200,
                    "timeout",
                ),
            ]
        )

        session.add(
            ProviderHealth(
                provider="jadlog_direct",
                consecutive_failures=3,
                quarantined_until=(
                    fixed_now
                    + timedelta(minutes=42)
                ),
            )
        )
        await session.commit()

        snapshot = (
            await build_admin_health_snapshot(
                session,
                now=fixed_now,
            )
        )

    await engine.dispose()

    assert snapshot.users == 1
    assert snapshot.shipments == 2
    assert snapshot.active_shipments == 1
    assert snapshot.active_subscriptions == 1
    assert snapshot.poll_backlog == 1

    assert snapshot.provider_queries_hour == 8
    by_provider = {
        item.name: item
        for item in snapshot.provider_metrics
    }
    assert by_provider[
        "correios_direct"
    ].queries == 4
    assert by_provider[
        "correios_direct"
    ].failures == 1
    assert by_provider[
        "correios_direct"
    ].avg_ms == 250
    assert snapshot.fastest_provider is not None
    assert (
        snapshot.fastest_provider.name
        == "correios_direct"
    )

    by_carrier = {
        item.name: item
        for item in snapshot.carrier_metrics
    }
    assert by_carrier["Correios"].queries == 7
    assert by_carrier["Jadlog"].avg_ms == 500

    assert len(snapshot.quarantined) == 1
    assert (
        snapshot.quarantined[0].name
        == "jadlog_direct"
    )

    assert snapshot.notification_sent_24h == 2
    assert snapshot.notification_failures_24h == 1
    assert snapshot.barcode_attempts_24h == 3
    assert snapshot.barcode_successes_24h == 1
    assert snapshot.barcode_misses_24h == 1
    assert snapshot.barcode_errors_24h == 1
    assert snapshot.ocr_attempts_24h == 2
    assert snapshot.ocr_successes_24h == 1
    assert snapshot.ocr_misses_24h == 0
    assert snapshot.ocr_errors_24h == 1

    by_failures = {
        item.name: item.failures
        for item in snapshot.provider_failures_24h
    }
    assert by_failures == {
        "correios_direct": 1
    }
    assert any(
        item.name == "correios_direct"
        and item.detail == "ProviderUnavailable"
        and item.count == 1
        for item in snapshot.provider_failure_causes_24h
    )

    # One provider error + one Telegram error + barcode error + OCR timeout.
    # A normal no_candidate scanner miss is intentionally not a technical error.
    assert snapshot.technical_errors_24h == 4

    rendered = render_admin_health(
        snapshot
    )
    assert "Saúde do Melhor Rastreio" in rendered
    assert "Correios direto" in rendered
    assert "Mais rápida" in rendered
    assert "Jadlog" in rendered
    assert "Quarentena" in rendered
    assert "QR/código de barras" in rendered
    assert "3</b> tentativas" in rendered
    assert "1</b> leitura direta" in rendered
    assert "fallback normal" in rendered
    assert "1 erro real" in rendered
    assert "Erros técnicos · 24h: 4" in rendered
    assert "Correios direto: <b>1</b>" in rendered
    assert "indisponibilidade da fonte (legado): 1" in rendered
    assert "Telegram/notificações: <b>1</b>" in rendered
    assert "Scanner QR/código de barras: <b>1</b>" in rendered
    assert "OCR: <b>1</b>" in rendered


@pytest.mark.asyncio
async def test_telemetry_pruning_keeps_recent_data(
    fixed_now,
):
    engine, Session = await _db()

    async with Session() as session:
        session.add_all(
            [
                OperationalEvent(
                    kind="provider_query",
                    name="old",
                    ok=True,
                    created_at=(
                        fixed_now
                        - timedelta(days=8)
                    ),
                ),
                OperationalEvent(
                    kind="provider_query",
                    name="recent",
                    ok=True,
                    created_at=(
                        fixed_now
                        - timedelta(days=2)
                    ),
                ),
            ]
        )
        await session.commit()

        deleted = await prune_operational_events(
            session,
            retention_days=7,
            now=fixed_now,
        )
        remaining = int(
            await session.scalar(
                select(
                    func.count(
                        OperationalEvent.id
                    )
                )
            )
            or 0
        )
        recent = await session.scalar(
            select(
                OperationalEvent.name
            )
        )

    await engine.dispose()

    assert deleted == 1
    assert remaining == 1
    assert recent == "recent"


class _FakeProvider:
    name = "fake_source"

    async def fetch(
        self,
        tracking_number,
        carrier_code=None,
        provider_tracking_id=None,
    ):
        return ProviderTracking(
            tracking_number=tracking_number,
            provider=self.name,
            carrier_name="Correios",
            events=[
                ProviderEvent(
                    status="in_transit",
                    status_raw="em trânsito",
                    description="Movimentação",
                    location="Campo Grande/MS",
                    event_at=datetime.now(
                        timezone.utc
                    ),
                )
            ],
        )


class _NotFoundProvider:
    name = "missing_source"

    async def fetch(
        self,
        tracking_number,
        carrier_code=None,
        provider_tracking_id=None,
    ):
        raise ProviderNotFound(
            "não encontrado"
        )


@pytest.mark.asyncio
async def test_provider_query_writes_privacy_safe_telemetry():
    engine, Session = await _db()
    service = TrackingService(
        Settings(
            melhor_rastreio_enabled=False,
            direct_fallbacks_enabled=False,
        )
    )
    provider = _FakeProvider()
    service._candidate_providers = (
        lambda shipment: [
            provider
        ]
    )

    async with Session() as session:
        shipment = Shipment(
            tracking_number="CC123456789BR",
            carrier_name="Correios",
            status="in_transit",
        )
        session.add(shipment)
        await session.commit()

        result = await service._query_best_provider(
            session,
            shipment,
        )
        await session.commit()

        metric = await session.scalar(
            select(
                OperationalEvent
            ).where(
                OperationalEvent.kind
                == "provider_query"
            )
        )

    await engine.dispose()

    assert result is not None
    assert metric is not None
    assert metric.name == "fake_source"
    assert metric.context_name == "Correios"
    assert metric.ok is True
    assert metric.duration_ms is not None

    # Telemetry fields do not contain the shipment identifier.
    assert (
        shipment.tracking_number
        not in (
            metric.detail
            or ""
        )
    )


@pytest.mark.asyncio
async def test_not_found_is_a_healthy_provider_response():
    engine, Session = await _db()
    service = TrackingService(
        Settings(
            melhor_rastreio_enabled=False,
            direct_fallbacks_enabled=False,
        )
    )
    provider = _NotFoundProvider()
    service._candidate_providers = (
        lambda shipment: [
            provider
        ]
    )

    async with Session() as session:
        shipment = Shipment(
            tracking_number="DD123456789BR",
            carrier_name="Correios",
            status="unknown",
        )
        session.add(shipment)
        await session.commit()

        result = await service._query_best_provider(
            session,
            shipment,
        )
        await session.commit()

        metric = await session.scalar(
            select(
                OperationalEvent
            ).where(
                OperationalEvent.name
                == "missing_source"
            )
        )

    await engine.dispose()

    assert result is None
    assert metric is not None
    assert metric.ok is True
    assert metric.detail == "not_found"



def test_provider_error_detail_classifies_sanitized_causes():
    assert (
        _provider_error_detail(
            asyncio.TimeoutError()
        )
        == "timeout"
    )

    request = httpx.Request(
        "POST",
        "https://example.invalid/graphql",
    )
    response = httpx.Response(
        502,
        request=request,
    )
    http_error = httpx.HTTPStatusError(
        "bad gateway",
        request=request,
        response=response,
    )
    wrapped_http = ProviderUnavailable(
        "fonte indisponível"
    )
    wrapped_http.__cause__ = http_error

    assert (
        _provider_error_detail(
            wrapped_http
        )
        == "http_502"
    )

    graphql_inner = ProviderUnavailable(
        "GraphQL temporarily unavailable"
    )
    wrapped_graphql = ProviderUnavailable(
        "fonte indisponível"
    )
    wrapped_graphql.__cause__ = graphql_inner

    assert (
        _provider_error_detail(
            wrapped_graphql
        )
        == "graphql_error"
    )

    invalid_json = ProviderUnavailable(
        "resposta inválida"
    )
    invalid_json.__cause__ = ValueError(
        "invalid json"
    )

    assert (
        _provider_error_detail(
            invalid_json
        )
        == "invalid_json"
    )
