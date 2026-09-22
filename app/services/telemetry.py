from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    and_,
    case,
    delete,
    func,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    OperationalEvent,
    PollingState,
    ProviderHealth,
    Shipment,
    Subscription,
    User,
)

log = logging.getLogger(__name__)

PROVIDER_LABELS = {
    "melhor_rastreio": "Melhor Rastreio",
    "rastreador_pacotes": "Rastreador Pacotes",
    "correios_direct": "Correios direto",
    "jadlog_direct": "Jadlog direto",
    "total_express_direct": "Total Express direto",
    "seventeen_track": "17TRACK",
    "ship24": "Ship24",
}


@dataclass(slots=True)
class ProviderMetric:
    name: str
    queries: int
    successes: int
    failures: int
    avg_ms: int | None


@dataclass(slots=True)
class QuarantinedProvider:
    name: str
    failures: int
    until: datetime


@dataclass(slots=True)
class AdminHealthSnapshot:
    generated_at: datetime
    users: int
    shipments: int
    active_shipments: int
    active_subscriptions: int
    poll_backlog: int
    provider_queries_hour: int
    provider_metrics: list[ProviderMetric]
    provider_failures_24h: list[ProviderMetric]
    carrier_metrics: list[ProviderMetric]
    fastest_provider: ProviderMetric | None
    quarantined: list[QuarantinedProvider]
    notification_sent_24h: int
    notification_failures_24h: int
    barcode_attempts_24h: int
    barcode_successes_24h: int
    barcode_misses_24h: int
    barcode_errors_24h: int
    ocr_attempts_24h: int
    ocr_successes_24h: int
    ocr_misses_24h: int
    ocr_errors_24h: int
    technical_errors_24h: int


def provider_label(name: str) -> str:
    return PROVIDER_LABELS.get(
        str(name or ""),
        str(name or "Fonte"),
    )


async def record_operational_event(
    session: AsyncSession,
    *,
    kind: str,
    name: str,
    ok: bool,
    duration_ms: int | float | None = None,
    detail: str | None = None,
    context_name: str | None = None,
) -> None:
    duration = None
    if duration_ms is not None:
        duration = max(
            0,
            min(
                int(round(duration_ms)),
                3_600_000,
            ),
        )

    session.add(
        OperationalEvent(
            kind=str(kind)[:40],
            name=str(name)[:80],
            ok=bool(ok),
            context_name=(
                str(context_name)[:120]
                if context_name
                else None
            ),
            duration_ms=duration,
            detail=(
                str(detail)[:120]
                if detail
                else None
            ),
        )
    )


async def record_runtime_event(
    *,
    kind: str,
    name: str,
    ok: bool,
    duration_ms: int | float | None = None,
    detail: str | None = None,
    context_name: str | None = None,
) -> None:
    """Persist one privacy-safe metric using its own short DB session."""

    try:
        from app.database import SessionLocal

        async with SessionLocal() as session:
            await record_operational_event(
                session,
                kind=kind,
                name=name,
                ok=ok,
                duration_ms=duration_ms,
                detail=detail,
                context_name=context_name,
            )
            await session.commit()
    except Exception:
        # Telemetry must never break the user-facing flow.
        log.exception(
            "Falha ao persistir telemetria operacional"
        )


def _as_utc(
    value: datetime,
) -> datetime:
    if value.tzinfo is None:
        return value.replace(
            tzinfo=timezone.utc
        )
    return value.astimezone(
        timezone.utc
    )


async def _provider_metrics(
    session: AsyncSession,
    *,
    since: datetime,
) -> list[ProviderMetric]:
    success_count = func.sum(
        case(
            (
                OperationalEvent.ok.is_(
                    True
                ),
                1,
            ),
            else_=0,
        )
    )
    failure_count = func.sum(
        case(
            (
                OperationalEvent.ok.is_(
                    False
                ),
                1,
            ),
            else_=0,
        )
    )
    success_latency = func.avg(
        case(
            (
                OperationalEvent.ok.is_(
                    True
                ),
                OperationalEvent.duration_ms,
            ),
            else_=None,
        )
    )

    rows = (
        await session.execute(
            select(
                OperationalEvent.name,
                func.count(
                    OperationalEvent.id
                ),
                success_count,
                failure_count,
                success_latency,
            )
            .where(
                OperationalEvent.kind
                == "provider_query",
                OperationalEvent.created_at
                >= since,
            )
            .group_by(
                OperationalEvent.name
            )
            .order_by(
                func.count(
                    OperationalEvent.id
                ).desc()
            )
        )
    ).all()

    return [
        ProviderMetric(
            name=str(name),
            queries=int(
                queries
                or 0
            ),
            successes=int(
                successes
                or 0
            ),
            failures=int(
                failures
                or 0
            ),
            avg_ms=(
                int(round(avg_ms))
                if avg_ms is not None
                else None
            ),
        )
        for (
            name,
            queries,
            successes,
            failures,
            avg_ms,
        ) in rows
    ]


async def _carrier_metrics(
    session: AsyncSession,
    *,
    since: datetime,
) -> list[ProviderMetric]:
    success_count = func.sum(
        case(
            (
                OperationalEvent.ok.is_(
                    True
                ),
                1,
            ),
            else_=0,
        )
    )
    failure_count = func.sum(
        case(
            (
                OperationalEvent.ok.is_(
                    False
                ),
                1,
            ),
            else_=0,
        )
    )
    success_latency = func.avg(
        case(
            (
                OperationalEvent.ok.is_(
                    True
                ),
                OperationalEvent.duration_ms,
            ),
            else_=None,
        )
    )

    rows = (
        await session.execute(
            select(
                OperationalEvent.context_name,
                func.count(
                    OperationalEvent.id
                ),
                success_count,
                failure_count,
                success_latency,
            )
            .where(
                OperationalEvent.kind
                == "provider_query",
                OperationalEvent.created_at
                >= since,
                OperationalEvent.context_name
                .is_not(None),
            )
            .group_by(
                OperationalEvent.context_name
            )
            .order_by(
                func.count(
                    OperationalEvent.id
                ).desc()
            )
            .limit(8)
        )
    ).all()

    return [
        ProviderMetric(
            name=str(name),
            queries=int(queries or 0),
            successes=int(successes or 0),
            failures=int(failures or 0),
            avg_ms=(
                int(round(avg_ms))
                if avg_ms is not None
                else None
            ),
        )
        for (
            name,
            queries,
            successes,
            failures,
            avg_ms,
        ) in rows
    ]


async def build_admin_health_snapshot(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> AdminHealthSnapshot:
    current = _as_utc(
        now
        or datetime.now(
            timezone.utc
        )
    )
    hour_cutoff = (
        current
        - timedelta(hours=1)
    )
    day_cutoff = (
        current
        - timedelta(hours=24)
    )

    users = int(
        await session.scalar(
            select(
                func.count(
                    User.id
                )
            )
        )
        or 0
    )
    shipments = int(
        await session.scalar(
            select(
                func.count(
                    Shipment.id
                )
            )
        )
        or 0
    )
    active_shipments = int(
        await session.scalar(
            select(
                func.count(
                    Shipment.id
                )
            ).where(
                Shipment.is_active.is_(
                    True
                )
            )
        )
        or 0
    )
    active_subscriptions = int(
        await session.scalar(
            select(
                func.count(
                    Subscription.id
                )
            )
            .join(
                Shipment,
                Shipment.id
                == Subscription.shipment_id,
            )
            .where(
                Subscription.is_active.is_(
                    True
                ),
                Shipment.status
                != "delivered",
            )
        )
        or 0
    )

    poll_backlog = int(
        await session.scalar(
            select(
                func.count(
                    PollingState.shipment_id
                )
            )
            .join(
                Shipment,
                Shipment.id
                == PollingState.shipment_id,
            )
            .where(
                Shipment.is_active.is_(
                    True
                ),
                PollingState.next_check_at
                .is_not(None),
                PollingState.next_check_at
                <= current,
            )
        )
        or 0
    )

    provider_metrics = (
        await _provider_metrics(
            session,
            since=hour_cutoff,
        )
    )
    provider_metrics_24h = (
        await _provider_metrics(
            session,
            since=day_cutoff,
        )
    )
    provider_failures_24h = sorted(
        (
            item
            for item in provider_metrics_24h
            if item.failures > 0
        ),
        key=lambda item: (
            -item.failures,
            -item.queries,
            item.name,
        ),
    )
    provider_queries_hour = sum(
        item.queries
        for item in provider_metrics
    )
    carrier_metrics = (
        await _carrier_metrics(
            session,
            since=hour_cutoff,
        )
    )

    eligible_fastest = [
        item
        for item in provider_metrics
        if (
            item.successes >= 3
            and item.avg_ms is not None
        )
    ]

    if not eligible_fastest:
        eligible_fastest = [
            item
            for item in provider_metrics_24h
            if (
                item.successes >= 3
                and item.avg_ms is not None
            )
        ]

    fastest_provider = (
        min(
            eligible_fastest,
            key=lambda item: (
                item.avg_ms
                if item.avg_ms
                is not None
                else 10**9
            ),
        )
        if eligible_fastest
        else None
    )

    quarantined_rows = (
        await session.scalars(
            select(
                ProviderHealth
            ).where(
                ProviderHealth.quarantined_until
                .is_not(None),
                ProviderHealth.quarantined_until
                > current,
            )
            .order_by(
                ProviderHealth.quarantined_until
            )
        )
    ).all()

    quarantined = [
        QuarantinedProvider(
            name=row.provider,
            failures=int(
                row.consecutive_failures
                or 0
            ),
            until=_as_utc(
                row.quarantined_until
            ),
        )
        for row in quarantined_rows
        if row.quarantined_until
        is not None
    ]

    notification_sent_24h = int(
        await session.scalar(
            select(
                func.count(
                    OperationalEvent.id
                )
            ).where(
                OperationalEvent.kind
                == "notification",
                OperationalEvent.created_at
                >= day_cutoff,
                OperationalEvent.ok.is_(
                    True
                ),
            )
        )
        or 0
    )
    notification_failures_24h = int(
        await session.scalar(
            select(
                func.count(
                    OperationalEvent.id
                )
            ).where(
                OperationalEvent.kind
                == "notification",
                OperationalEvent.created_at
                >= day_cutoff,
                OperationalEvent.ok.is_(
                    False
                ),
            )
        )
        or 0
    )

    async def scan_counts(
        name: str,
    ) -> tuple[
        int,
        int,
        int,
        int,
    ]:
        base = (
            OperationalEvent.kind
            == "image_scan",
            OperationalEvent.name
            == name,
            OperationalEvent.created_at
            >= day_cutoff,
        )

        total = int(
            await session.scalar(
                select(
                    func.count(
                        OperationalEvent.id
                    )
                ).where(
                    *base
                )
            )
            or 0
        )
        success = int(
            await session.scalar(
                select(
                    func.count(
                        OperationalEvent.id
                    )
                ).where(
                    *base,
                    OperationalEvent.ok.is_(
                        True
                    ),
                )
            )
            or 0
        )
        misses = int(
            await session.scalar(
                select(
                    func.count(
                        OperationalEvent.id
                    )
                ).where(
                    *base,
                    OperationalEvent.detail
                    == "no_candidate",
                )
            )
            or 0
        )
        errors = int(
            await session.scalar(
                select(
                    func.count(
                        OperationalEvent.id
                    )
                ).where(
                    *base,
                    OperationalEvent.detail
                    .in_(
                        [
                            "error",
                            "timeout",
                        ]
                    ),
                )
            )
            or 0
        )

        return (
            total,
            success,
            misses,
            errors,
        )

    (
        barcode_attempts_24h,
        barcode_successes_24h,
        barcode_misses_24h,
        barcode_errors_24h,
    ) = await scan_counts(
        "barcode"
    )
    (
        ocr_attempts_24h,
        ocr_successes_24h,
        ocr_misses_24h,
        ocr_errors_24h,
    ) = await scan_counts(
        "ocr"
    )

    provider_errors_24h = sum(
        item.failures
        for item in provider_failures_24h
    )
    technical_errors_24h = (
        provider_errors_24h
        + notification_failures_24h
        + barcode_errors_24h
        + ocr_errors_24h
    )

    return AdminHealthSnapshot(
        generated_at=current,
        users=users,
        shipments=shipments,
        active_shipments=active_shipments,
        active_subscriptions=active_subscriptions,
        poll_backlog=poll_backlog,
        provider_queries_hour=provider_queries_hour,
        provider_metrics=provider_metrics,
        provider_failures_24h=provider_failures_24h,
        carrier_metrics=carrier_metrics,
        fastest_provider=fastest_provider,
        quarantined=quarantined,
        notification_sent_24h=notification_sent_24h,
        notification_failures_24h=notification_failures_24h,
        barcode_attempts_24h=barcode_attempts_24h,
        barcode_successes_24h=barcode_successes_24h,
        barcode_misses_24h=barcode_misses_24h,
        barcode_errors_24h=barcode_errors_24h,
        ocr_attempts_24h=ocr_attempts_24h,
        ocr_successes_24h=ocr_successes_24h,
        ocr_misses_24h=ocr_misses_24h,
        ocr_errors_24h=ocr_errors_24h,
        technical_errors_24h=technical_errors_24h,
    )


async def prune_operational_events(
    session: AsyncSession,
    *,
    retention_days: int,
    now: datetime | None = None,
) -> int:
    current = _as_utc(
        now
        or datetime.now(
            timezone.utc
        )
    )
    cutoff = (
        current
        - timedelta(
            days=max(
                1,
                int(
                    retention_days
                ),
            )
        )
    )

    result = await session.execute(
        delete(
            OperationalEvent
        ).where(
            OperationalEvent.created_at
            < cutoff
        )
    )
    await session.commit()

    return int(
        result.rowcount
        or 0
    )


def percentage(
    successes: int,
    total: int,
) -> str:
    if total <= 0:
        return "—"
    value = (
        max(
            0,
            successes,
        )
        / total
        * 100
    )
    return f"{value:.0f}%"
