from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

from app.models import Base, Shipment, TrackingEvent
from app.services.insights import (
    action_advice,
    estimate_delivery_window,
    format_delivery_estimate,
    package_bucket,
)


@pytest.mark.asyncio
async def test_delivery_estimate_uses_similar_status_history():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )
    now = datetime.now(timezone.utc)

    async with Session() as session:
        for index, remaining_hours in enumerate(
            [8, 10, 12, 14, 16, 18],
            1,
        ):
            delivered_at = (
                now
                - timedelta(days=index)
            )
            status_at = (
                delivered_at
                - timedelta(hours=remaining_hours)
            )
            shipment = Shipment(
                tracking_number=f"AA{index:09d}BR",
                carrier_name="Transportadora Teste",
                provider="test",
                status="delivered",
                registered_at=status_at - timedelta(days=2),
                last_event_at=delivered_at,
                delivered_at=delivered_at,
                is_active=False,
            )
            session.add(shipment)
            await session.flush()
            session.add(
                TrackingEvent(
                    shipment_id=shipment.id,
                    event_hash=f"hash-{index}",
                    status="in_transit",
                    description="Em trânsito",
                    event_at=status_at,
                )
            )

        target = Shipment(
            tracking_number="ZZ123456789BR",
            carrier_name="Transportadora Teste",
            provider="test",
            status="in_transit",
            registered_at=now - timedelta(days=1),
            last_event_at=now - timedelta(hours=1),
        )
        session.add(target)
        await session.commit()

        estimate = await estimate_delivery_window(
            session,
            target,
        )

    await engine.dispose()

    assert estimate is not None
    assert estimate.sample_size == 6
    assert estimate.start < estimate.end
    assert estimate.start > now
    formatted = format_delivery_estimate(
        estimate,
        "America/Sao_Paulo",
    )
    assert formatted
    assert "entregas semelhantes" in formatted


def test_package_bucket_prioritizes_attention_and_today():
    now = datetime.now(timezone.utc)

    attention = SimpleNamespace(
        status="customs",
        last_event_at=now,
        registered_at=now,
        delivered_at=None,
    )
    assert (
        package_bucket(
            attention,
            stale_after_hours=72,
            now=now,
        )
        == "attention"
    )

    delivery = SimpleNamespace(
        status="out_for_delivery",
        last_event_at=now,
        registered_at=now,
        delivered_at=None,
    )
    assert (
        package_bucket(
            delivery,
            stale_after_hours=72,
            now=now,
        )
        == "out_for_delivery"
    )


def test_action_advice_is_actionable_for_problem_status():
    title, body = action_advice("customs")

    assert "canais oficiais" in title.lower()
    assert "cobran" in body.lower()
