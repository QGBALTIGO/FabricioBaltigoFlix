from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.models import (
    Shipment,
    Subscription,
    TrackingEvent,
    User,
)
from app.providers.base import ProviderTracking
from app.providers.seventeen_track import (
    SeventeenTrackProvider,
)
from app.providers.ship24 import Ship24Provider
from app.status import normalize_status
from app.utils import (
    event_hash,
    normalize_tracking_number,
)


@dataclass(slots=True)
class AddResult:
    subscription: Subscription
    created_shipment: bool
    warning: str | None = None


class TrackingService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.seventeen = (
            SeventeenTrackProvider(
                settings.seventeen_track_token,
                settings.http_timeout_seconds,
            )
            if settings.seventeen_track_token
            else None
        )
        self.ship24 = (
            Ship24Provider(
                settings.ship24_api_key,
                settings.http_timeout_seconds,
            )
            if settings.ship24_api_key
            else None
        )

    async def ensure_user(
        self,
        session: AsyncSession,
        telegram_id: int,
        username: str | None = None,
        first_name: str | None = None,
    ) -> User:
        user = await session.scalar(
            select(User).where(
                User.telegram_id == telegram_id
            )
        )
        if user:
            user.username = username
            user.first_name = first_name
            return user

        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
        )
        session.add(user)
        await session.flush()
        return user

    async def _check_user_limit(
        self,
        session: AsyncSession,
        user_id: int,
    ) -> None:
        active_count = await session.scalar(
            select(func.count(Subscription.id)).where(
                Subscription.user_id == user_id,
                Subscription.is_active.is_(True),
            )
        )
        if (
            active_count or 0
        ) >= self.settings.max_active_shipments_per_user:
            raise ValueError(
                "Limite de "
                f"{self.settings.max_active_shipments_per_user} "
                "rastreios ativos atingido."
            )

    async def add_tracking(
        self,
        session: AsyncSession,
        user: User,
        tracking_number: str,
        nickname: str | None = None,
    ) -> AddResult:
        number = normalize_tracking_number(
            tracking_number
        )
        shipment = await session.scalar(
            select(Shipment).where(
                Shipment.tracking_number == number
            )
        )
        created = shipment is None
        warning = None

        if shipment is None:
            await self._check_user_limit(
                session,
                user.id,
            )
            shipment = Shipment(
                tracking_number=number
            )
            session.add(shipment)
            await session.flush()

            try:
                provider_data = (
                    await self._register_provider(
                        number
                    )
                )
                if provider_data:
                    await self._apply_provider_data(
                        session,
                        shipment,
                        provider_data,
                    )
                    try:
                        detailed = (
                            await self._fetch_provider(
                                shipment
                            )
                        )
                        if detailed:
                            await self._apply_provider_data(
                                session,
                                shipment,
                                detailed,
                            )
                    except Exception:
                        # O registro já foi aceito.
                        # A primeira atualização pode
                        # chegar pelo webhook.
                        pass
                else:
                    warning = (
                        "Nenhum provedor de rastreio "
                        "está configurado no servidor."
                    )
            except Exception as exc:
                warning = (
                    str(exc)
                    or (
                        "Não foi possível consultar a "
                        "transportadora agora."
                    )
                )

        subscription = await session.scalar(
            select(Subscription)
            .options(
                selectinload(
                    Subscription.shipment
                )
            )
            .where(
                Subscription.user_id == user.id,
                Subscription.shipment_id
                == shipment.id,
            )
        )

        if subscription is None:
            await self._check_user_limit(
                session,
                user.id,
            )
            subscription = Subscription(
                user_id=user.id,
                shipment_id=shipment.id,
                nickname=nickname,
                notify_level=(
                    self.settings.default_notify_level
                ),
            )
            session.add(subscription)
        else:
            subscription.is_active = True
            if nickname:
                subscription.nickname = nickname

        await session.commit()
        subscription = await self.get_subscription(
            session,
            user.id,
            subscription.id,
        )
        return AddResult(
            subscription=subscription,
            created_shipment=created,
            warning=warning,
        )

    async def follow_existing_shipment(
        self,
        session: AsyncSession,
        user: User,
        shipment_id: int,
    ) -> Subscription | None:
        shipment = await session.get(
            Shipment,
            shipment_id,
        )
        if not shipment:
            return None

        sub = await session.scalar(
            select(Subscription).where(
                Subscription.user_id == user.id,
                Subscription.shipment_id
                == shipment_id,
            )
        )
        if sub is None:
            await self._check_user_limit(
                session,
                user.id,
            )
            sub = Subscription(
                user_id=user.id,
                shipment_id=shipment_id,
                notify_level=(
                    self.settings.default_notify_level
                ),
                notifications_enabled=True,
                is_active=True,
            )
            session.add(sub)
        else:
            sub.is_active = True
            sub.notifications_enabled = True
            if sub.notify_level == "off":
                sub.notify_level = (
                    self.settings.default_notify_level
                )

        await session.commit()
        return await self.get_subscription(
            session,
            user.id,
            sub.id,
        )

    async def _register_provider(
        self,
        number: str,
    ) -> ProviderTracking | None:
        last_error: Exception | None = None

        if self.seventeen:
            try:
                return await self.seventeen.register(
                    number
                )
            except Exception as exc:
                last_error = exc

        if self.ship24:
            try:
                return await self.ship24.register(
                    number
                )
            except Exception as exc:
                last_error = exc

        if last_error:
            raise last_error
        return None

    async def _fetch_provider(
        self,
        shipment: Shipment,
    ) -> ProviderTracking | None:
        if (
            shipment.provider == "17track"
            and self.seventeen
        ):
            return await self.seventeen.fetch(
                shipment.tracking_number,
                shipment.carrier_code,
                shipment.provider_tracking_id,
            )
        if (
            shipment.provider == "ship24"
            and self.ship24
        ):
            return await self.ship24.fetch(
                shipment.tracking_number,
                shipment.carrier_code,
                shipment.provider_tracking_id,
            )
        return await self._register_provider(
            shipment.tracking_number
        )

    async def refresh_shipment(
        self,
        session: AsyncSession,
        shipment: Shipment,
    ) -> ProviderTracking | None:
        data, _ = await self.refresh_with_events(
            session,
            shipment,
        )
        return data

    async def refresh_with_events(
        self,
        session: AsyncSession,
        shipment: Shipment,
    ) -> tuple[
        ProviderTracking | None,
        list[TrackingEvent],
    ]:
        provider_data = await self._fetch_provider(
            shipment
        )
        new_events: list[TrackingEvent] = []
        if provider_data:
            new_events = (
                await self._apply_provider_data(
                    session,
                    shipment,
                    provider_data,
                )
            )
            await session.commit()
        return provider_data, new_events

    async def apply_webhook(
        self,
        session: AsyncSession,
        data: ProviderTracking,
    ) -> tuple[
        Shipment | None,
        list[TrackingEvent],
    ]:
        shipment = await session.scalar(
            select(Shipment).where(
                Shipment.tracking_number
                == normalize_tracking_number(
                    data.tracking_number
                )
            )
        )
        if not shipment:
            return None, []

        new_events = await self._apply_provider_data(
            session,
            shipment,
            data,
        )
        await session.commit()
        return shipment, new_events

    async def _apply_provider_data(
        self,
        session: AsyncSession,
        shipment: Shipment,
        data: ProviderTracking,
    ) -> list[TrackingEvent]:
        shipment.provider = (
            data.provider
            or shipment.provider
        )
        shipment.provider_tracking_id = (
            data.provider_tracking_id
            or shipment.provider_tracking_id
        )
        shipment.carrier_code = (
            data.carrier_code
            or shipment.carrier_code
        )
        shipment.carrier_name = (
            data.carrier_name
            or shipment.carrier_name
        )
        shipment.status_raw = (
            data.status_raw
            or shipment.status_raw
        )

        try:
            shipment.extra_json = json.dumps(
                data.raw,
                ensure_ascii=False,
            )[:20000]
        except Exception:
            pass

        new_events: list[TrackingEvent] = []

        for ev in data.events:
            h = event_hash(
                shipment.tracking_number,
                ev.status,
                ev.description,
                ev.location,
                ev.event_at,
            )
            exists = await session.scalar(
                select(TrackingEvent.id).where(
                    TrackingEvent.shipment_id
                    == shipment.id,
                    TrackingEvent.event_hash == h,
                )
            )
            if exists:
                continue

            model = TrackingEvent(
                shipment_id=shipment.id,
                event_hash=h,
                status=ev.status,
                status_raw=ev.status_raw,
                description=ev.description,
                location=ev.location,
                event_at=ev.event_at,
            )
            session.add(model)
            new_events.append(model)

        if data.events:
            latest = max(
                data.events,
                key=lambda x: x.event_at,
            )
            shipment.status = latest.status
            shipment.status_raw = (
                latest.status_raw
                or data.status_raw
            )
            shipment.last_description = (
                latest.description
            )
            shipment.last_location = (
                latest.location
            )
            shipment.last_event_at = (
                latest.event_at
            )
            shipment.is_active = (
                latest.status != "delivered"
            )
            if latest.status == "delivered":
                shipment.delivered_at = (
                    latest.event_at
                )

        elif data.status_raw:
            shipment.status = normalize_status(
                data.status_raw
            )

        elif (
            data.provider
            and shipment.status == "unknown"
        ):
            shipment.status = "info_received"

        return new_events

    async def find_subscriptions(
        self,
        session: AsyncSession,
        user_id: int,
        *,
        delivered: bool | None = False,
        status: str | None = None,
        carrier: str | None = None,
        query: str | None = None,
        page: int = 0,
        page_size: int | None = None,
    ) -> tuple[list[Subscription], int]:
        page_size = (
            page_size
            or self.settings.list_page_size
        )
        conditions = [
            Subscription.user_id == user_id,
            Subscription.is_active.is_(True),
        ]

        if delivered is True:
            conditions.append(
                Shipment.status == "delivered"
            )
        elif delivered is False:
            conditions.append(
                Shipment.status != "delivered"
            )

        if status:
            conditions.append(
                Shipment.status == status
            )
        if carrier:
            conditions.append(
                func.lower(
                    Shipment.carrier_name
                )
                == carrier.lower()
            )
        if query:
            q = f"%{query.strip()}%"
            conditions.append(
                or_(
                    Shipment.tracking_number.ilike(
                        q
                    ),
                    Shipment.carrier_name.ilike(
                        q
                    ),
                    Shipment.status.ilike(q),
                    Shipment.status_raw.ilike(
                        q
                    ),
                    Shipment.last_description.ilike(
                        q
                    ),
                    Subscription.nickname.ilike(q),
                )
            )

        count_stmt = (
            select(
                func.count(
                    Subscription.id
                )
            )
            .join(Shipment)
            .where(*conditions)
        )
        total = int(
            await session.scalar(count_stmt)
            or 0
        )

        stmt = (
            select(Subscription)
            .options(
                selectinload(
                    Subscription.shipment
                )
            )
            .join(Shipment)
            .where(*conditions)
            .order_by(
                Shipment.last_event_at
                .desc()
                .nullslast(),
                Subscription.id.desc(),
            )
            .offset(
                max(0, page)
                * page_size
            )
            .limit(page_size)
        )

        return (
            list(
                (
                    await session.scalars(
                        stmt
                    )
                ).all()
            ),
            total,
        )

    async def list_subscriptions(
        self,
        session: AsyncSession,
        user_id: int,
        delivered: bool = False,
    ) -> list[Subscription]:
        items, _ = await self.find_subscriptions(
            session,
            user_id,
            delivered=delivered,
            page=0,
            page_size=30,
        )
        return items

    async def carriers_for_user(
        self,
        session: AsyncSession,
        user_id: int,
    ) -> list[str]:
        stmt = (
            select(
                Shipment.carrier_name
            )
            .join(
                Subscription,
                Subscription.shipment_id
                == Shipment.id,
            )
            .where(
                Subscription.user_id == user_id,
                Subscription.is_active.is_(True),
                Shipment.carrier_name.is_not(
                    None
                ),
            )
            .distinct()
            .order_by(
                Shipment.carrier_name
            )
        )

        return [
            str(x)
            for x in (
                await session.scalars(stmt)
            ).all()
            if x
        ]

    async def get_subscription(
        self,
        session: AsyncSession,
        user_id: int,
        subscription_id: int,
    ) -> Subscription | None:
        return await session.scalar(
            select(Subscription)
            .options(
                selectinload(
                    Subscription.shipment
                )
            )
            .where(
                Subscription.id
                == subscription_id,
                Subscription.user_id
                == user_id,
            )
        )

    async def history(
        self,
        session: AsyncSession,
        shipment_id: int,
        limit: int = 20,
    ) -> list[TrackingEvent]:
        return list(
            (
                await session.scalars(
                    select(TrackingEvent)
                    .where(
                        TrackingEvent.shipment_id
                        == shipment_id
                    )
                    .order_by(
                        TrackingEvent.event_at
                        .desc()
                    )
                    .limit(limit)
                )
            ).all()
        )

    async def active_shipments_for_polling(
        self,
        session: AsyncSession,
    ) -> list[Shipment]:
        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(
                days=(
                    self.settings
                    .poll_tracking_days
                )
            )
        )

        return list(
            (
                await session.scalars(
                    select(Shipment).where(
                        Shipment.is_active.is_(
                            True
                        ),
                        Shipment.status
                        != "delivered",
                        Shipment.registered_at
                        >= cutoff,
                    )
                )
            ).all()
        )
