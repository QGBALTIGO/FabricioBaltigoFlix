from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.models import Shipment, Subscription, TrackingEvent, User
from app.providers.base import ProviderTracking
from app.providers.seventeen_track import (
    SeventeenTrackError,
    SeventeenTrackProvider,
)
from app.providers.ship24 import Ship24Error, Ship24Provider
from app.status import normalize_status
from app.utils import event_hash, normalize_tracking_number


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
            select(User).where(User.telegram_id == telegram_id)
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

    async def add_tracking(
        self,
        session: AsyncSession,
        user: User,
        tracking_number: str,
        nickname: str | None = None,
    ) -> AddResult:
        number = normalize_tracking_number(tracking_number)
        shipment = await session.scalar(
            select(Shipment).where(
                Shipment.tracking_number == number
            )
        )
        created = shipment is None
        warning = None

        if shipment is None:
            active_count = await session.scalar(
                select(func.count(Subscription.id)).where(
                    Subscription.user_id == user.id,
                    Subscription.is_active.is_(True),
                )
            )
            if (
                active_count or 0
            ) >= self.settings.max_active_shipments_per_user:
                raise ValueError(
                    f"Limite de {self.settings.max_active_shipments_per_user} "
                    "rastreios ativos atingido."
                )

            shipment = Shipment(tracking_number=number)
            session.add(shipment)
            await session.flush()

            try:
                provider_data = await self._register_provider(number)
                if provider_data:
                    await self._apply_provider_data(
                        session,
                        shipment,
                        provider_data,
                    )
                else:
                    warning = (
                        "Nenhum provedor de rastreio está configurado "
                        "no servidor."
                    )
            except Exception as exc:
                warning = (
                    str(exc)
                    or "Não foi possível consultar a transportadora agora."
                )

        subscription = await session.scalar(
            select(Subscription)
            .options(selectinload(Subscription.shipment))
            .where(
                Subscription.user_id == user.id,
                Subscription.shipment_id == shipment.id,
            )
        )

        if subscription is None:
            subscription = Subscription(
                user_id=user.id,
                shipment_id=shipment.id,
                nickname=nickname,
                notify_level=self.settings.default_notify_level,
            )
            session.add(subscription)
        else:
            subscription.is_active = True
            if nickname:
                subscription.nickname = nickname

        await session.commit()

        subscription = await session.scalar(
            select(Subscription)
            .options(selectinload(Subscription.shipment))
            .where(
                Subscription.user_id == user.id,
                Subscription.shipment_id == shipment.id,
            )
        )

        return AddResult(
            subscription=subscription,
            created_shipment=created,
            warning=warning,
        )

    async def _register_provider(
        self,
        number: str,
    ) -> ProviderTracking | None:
        last_error: Exception | None = None

        if self.seventeen:
            try:
                return await self.seventeen.register(number)
            except Exception as exc:
                last_error = exc

        if self.ship24:
            try:
                return await self.ship24.register(number)
            except Exception as exc:
                last_error = exc

        if last_error:
            raise last_error

        return None

    async def refresh_shipment(
        self,
        session: AsyncSession,
        shipment: Shipment,
    ) -> ProviderTracking | None:
        provider_data: ProviderTracking | None = None

        if shipment.provider == "17track" and self.seventeen:
            provider_data = await self.seventeen.fetch(
                shipment.tracking_number,
                shipment.carrier_code,
                shipment.provider_tracking_id,
            )
        elif shipment.provider == "ship24" and self.ship24:
            provider_data = await self.ship24.fetch(
                shipment.tracking_number,
                shipment.carrier_code,
                shipment.provider_tracking_id,
            )
        else:
            provider_data = await self._register_provider(
                shipment.tracking_number
            )

        if provider_data:
            await self._apply_provider_data(
                session,
                shipment,
                provider_data,
            )
            await session.commit()

        return provider_data

    async def apply_webhook(
        self,
        session: AsyncSession,
        data: ProviderTracking,
    ) -> tuple[Shipment | None, list[TrackingEvent]]:
        shipment = await session.scalar(
            select(Shipment).where(
                Shipment.tracking_number
                == normalize_tracking_number(data.tracking_number)
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
        shipment.provider = data.provider or shipment.provider
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
                    TrackingEvent.shipment_id == shipment.id,
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
            shipment.last_description = latest.description
            shipment.last_location = latest.location
            shipment.last_event_at = latest.event_at

            if latest.status == "delivered":
                shipment.delivered_at = latest.event_at
                shipment.is_active = False

        elif data.status_raw:
            shipment.status = normalize_status(data.status_raw)

        elif data.provider and shipment.status == "unknown":
            shipment.status = "info_received"

        return new_events

    async def list_subscriptions(
        self,
        session: AsyncSession,
        user_id: int,
        delivered: bool = False,
    ) -> list[Subscription]:
        stmt = (
            select(Subscription)
            .options(selectinload(Subscription.shipment))
            .join(Shipment)
            .where(
                Subscription.user_id == user_id,
                Subscription.is_active.is_(True),
            )
        )

        if delivered:
            stmt = stmt.where(Shipment.status == "delivered")
        else:
            stmt = stmt.where(Shipment.status != "delivered")

        stmt = stmt.order_by(
            Shipment.last_event_at.desc().nullslast(),
            Subscription.id.desc(),
        )

        return list((await session.scalars(stmt)).all())

    async def get_subscription(
        self,
        session: AsyncSession,
        user_id: int,
        subscription_id: int,
    ) -> Subscription | None:
        return await session.scalar(
            select(Subscription)
            .options(selectinload(Subscription.shipment))
            .where(
                Subscription.id == subscription_id,
                Subscription.user_id == user_id,
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
                        TrackingEvent.shipment_id == shipment_id
                    )
                    .order_by(
                        TrackingEvent.event_at.desc()
                    )
                    .limit(limit)
                )
            ).all()
        )
