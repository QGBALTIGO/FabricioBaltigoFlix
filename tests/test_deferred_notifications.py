from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

from app.models import (
    Base,
    DeferredNotification,
    Shipment,
    Subscription,
    TrackingEvent,
    User,
    UserPreference,
)
from app.services import notifier


async def _fixture(session):
    user = User(
        telegram_id=123456,
        first_name="Teste",
    )
    session.add(user)
    await session.flush()

    shipment = Shipment(
        tracking_number="AB123456789BR",
        carrier_name="Correios",
        status="in_transit",
    )
    session.add(shipment)
    await session.flush()

    sub = Subscription(
        user_id=user.id,
        shipment_id=shipment.id,
        notify_level="all",
        notifications_enabled=True,
    )
    session.add(sub)
    await session.flush()

    event = TrackingEvent(
        shipment_id=shipment.id,
        event_hash="legacy-quiet-test",
        status="in_transit",
        description="Em trânsito",
        event_at=datetime.now(timezone.utc),
    )
    session.add(event)
    session.add(
        UserPreference(
            user_id=user.id,
            quiet_hours_enabled=True,
        )
    )
    await session.commit()
    return user, shipment, sub, event


@pytest.mark.asyncio
async def test_legacy_quiet_preference_no_longer_delays_new_alert(
    monkeypatch,
):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )

    async with Session() as session:
        user, shipment, sub, event = await _fixture(
            session
        )
        delivered = []

        async def record_send(
            bot,
            subscription,
            current_shipment,
            current_event,
            *,
            telegram_id=None,
            new_events_count=1,
        ):
            delivered.append(
                (
                    subscription.id,
                    current_event.id,
                    new_events_count,
                )
            )

        monkeypatch.setattr(
            notifier,
            "_send_event",
            record_send,
        )

        sent = await notifier.notify_new_events(
            session,
            object(),
            shipment,
            [event],
        )

        assert sent == 1
        assert delivered == [
            (
                sub.id,
                event.id,
                1,
            )
        ]
        queued = int(
            await session.scalar(
                select(
                    func.count(
                        DeferredNotification.id
                    )
                )
            )
            or 0
        )
        assert queued == 0

    await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_deferred_queue_is_drained_without_quiet_rules(
    monkeypatch,
):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )

    async with Session() as session:
        user, shipment, sub, event = await _fixture(
            session
        )

        session.add(
            DeferredNotification(
                subscription_id=sub.id,
                event_id=event.id,
                event_count=2,
                deliver_after=(
                    datetime.now(timezone.utc)
                    - timedelta(seconds=1)
                ),
            )
        )
        await session.commit()

        delivered = []

        async def record_send(
            bot,
            subscription,
            current_shipment,
            current_event,
            *,
            telegram_id=None,
            new_events_count=1,
        ):
            delivered.append(
                (
                    subscription.id,
                    current_event.id,
                    telegram_id,
                    new_events_count,
                )
            )

        monkeypatch.setattr(
            notifier,
            "_send_event",
            record_send,
        )

        count = (
            await notifier
            .send_due_deferred_notifications(
                session,
                object(),
            )
        )

        assert count == 1
        assert delivered == [
            (
                sub.id,
                event.id,
                user.telegram_id,
                2,
            )
        ]
        remaining = int(
            await session.scalar(
                select(
                    func.count(
                        DeferredNotification.id
                    )
                )
            )
            or 0
        )
        assert remaining == 0

    await engine.dispose()
