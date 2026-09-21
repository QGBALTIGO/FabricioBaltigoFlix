from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings
from app.models import (
    Base,
    Shipment,
    Subscription,
    User,
)
from app.services.archive import (
    delivered_age_label,
)
from app.services.tracking import TrackingService


@pytest.mark.asyncio
async def test_recent_and_archived_deliveries_are_split_automatically():
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
    settings = Settings(
        melhor_rastreio_enabled=False,
        direct_fallbacks_enabled=False,
        delivered_archive_after_days=7,
    )
    service = TrackingService(
        settings
    )
    now = datetime.now(
        timezone.utc
    )

    async with Session() as session:
        user = User(
            telegram_id=123,
            first_name="Teste",
        )
        session.add(user)
        await session.flush()

        recent = Shipment(
            tracking_number="AA123456789BR",
            carrier_name="Correios",
            status="delivered",
            is_active=False,
            delivered_at=(
                now
                - timedelta(days=2)
            ),
        )
        old = Shipment(
            tracking_number="BB123456789BR",
            carrier_name="Correios",
            status="delivered",
            is_active=False,
            delivered_at=(
                now
                - timedelta(days=10)
            ),
        )
        session.add_all(
            [recent, old]
        )
        await session.flush()

        session.add_all(
            [
                Subscription(
                    user_id=user.id,
                    shipment_id=recent.id,
                    nickname="Recente",
                ),
                Subscription(
                    user_id=user.id,
                    shipment_id=old.id,
                    nickname="Antiga",
                ),
            ]
        )
        await session.commit()

        recent_rows, recent_total = (
            await service.find_subscriptions(
                session,
                user.id,
                delivered=True,
                archived=False,
            )
        )
        archive_rows, archive_total = (
            await service.find_subscriptions(
                session,
                user.id,
                delivered=True,
                archived=True,
            )
        )

        assert recent_total == 1
        assert [
            item.nickname
            for item in recent_rows
        ] == ["Recente"]

        assert archive_total == 1
        assert [
            item.nickname
            for item in archive_rows
        ] == ["Antiga"]

    await engine.dispose()


@pytest.mark.asyncio
async def test_delivered_packages_do_not_consume_active_limit():
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
    settings = Settings(
        melhor_rastreio_enabled=False,
        direct_fallbacks_enabled=False,
        max_active_shipments_per_user=1,
    )
    service = TrackingService(
        settings
    )

    async with Session() as session:
        user = User(
            telegram_id=456,
            first_name="Teste",
        )
        session.add(user)
        await session.flush()

        delivered = Shipment(
            tracking_number="CC123456789BR",
            status="delivered",
            is_active=False,
            delivered_at=datetime.now(
                timezone.utc
            ),
        )
        session.add(delivered)
        await session.flush()
        session.add(
            Subscription(
                user_id=user.id,
                shipment_id=delivered.id,
            )
        )
        await session.commit()

        # One delivered package must not consume the single active slot.
        await service._check_user_limit(
            session,
            user.id,
        )

        active = Shipment(
            tracking_number="DD123456789BR",
            status="in_transit",
            is_active=True,
        )
        session.add(active)
        await session.flush()
        session.add(
            Subscription(
                user_id=user.id,
                shipment_id=active.id,
            )
        )
        await session.commit()

        with pytest.raises(
            ValueError,
            match="Limite de 1",
        ):
            await service._check_user_limit(
                session,
                user.id,
            )

    await engine.dispose()


def test_delivery_age_label_is_human_and_calendar_based():
    now = datetime(
        2026,
        9,
        21,
        18,
        0,
        tzinfo=timezone.utc,
    )

    today = Shipment(
        tracking_number="EE123456789BR",
        delivered_at=datetime(
            2026,
            9,
            21,
            12,
            0,
            tzinfo=timezone.utc,
        ),
    )
    yesterday = Shipment(
        tracking_number="FF123456789BR",
        delivered_at=datetime(
            2026,
            9,
            20,
            12,
            0,
            tzinfo=timezone.utc,
        ),
    )
    older = Shipment(
        tracking_number="GG123456789BR",
        delivered_at=datetime(
            2026,
            9,
            18,
            12,
            0,
            tzinfo=timezone.utc,
        ),
    )

    assert (
        delivered_age_label(
            today,
            "UTC",
            now=now,
        )
        == "entregue hoje"
    )
    assert (
        delivered_age_label(
            yesterday,
            "UTC",
            now=now,
        )
        == "entregue ontem"
    )
    assert (
        delivered_age_label(
            older,
            "UTC",
            now=now,
        )
        == "entregue há 3 dias"
    )
