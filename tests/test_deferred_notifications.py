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


@pytest.mark.asyncio
async def test_quiet_event_is_persisted_and_delivered_later(
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
            event_hash="quiet-test",
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

        future = (
            datetime.now(timezone.utc)
            + timedelta(hours=2)
        )
        monkeypatch.setattr(
            notifier,
            "quiet_window",
            lambda *args, **kwargs: (
                True,
                future,
            ),
        )

        async def should_not_send(*args, **kwargs):
            raise AssertionError(
                "quiet notification sent immediately"
            )

        monkeypatch.setattr(
            notifier,
            "_send_event",
            should_not_send,
        )

        sent = await notifier.notify_new_events(
            session,
            object(),
            shipment,
            [event],
        )

        assert sent == 0
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
        assert queued == 1

        deferred = await session.scalar(
            select(DeferredNotification)
        )
        deferred.deliver_after = (
            datetime.now(timezone.utc)
            - timedelta(seconds=1)
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
                )
            )

        monkeypatch.setattr(
            notifier,
            "quiet_window",
            lambda *args, **kwargs: (
                False,
                None,
            ),
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
