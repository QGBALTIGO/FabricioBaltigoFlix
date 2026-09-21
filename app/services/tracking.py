from __future__ import annotations

import asyncio
import json
import weakref
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.models import (
    PollingState,
    ProviderHealth,
    Shipment,
    Subscription,
    TrackingEvent,
    User,
)
from app.providers.base import (
    ProviderNotFound,
    ProviderTracking,
    ProviderUnavailable,
)
from app.providers.correios_direct import CorreiosDirectProvider
from app.providers.jadlog_direct import JadlogDirectProvider
from app.providers.melhor_rastreio import MelhorRastreioProvider
from app.providers.rastreador_pacotes import RastreadorPacotesProvider
from app.providers.seventeen_track import SeventeenTrackProvider
from app.providers.ship24 import Ship24Provider
from app.providers.total_express_direct import TotalExpressDirectProvider
from app.services.archive import (
    archive_cutoff,
    delivered_reference_sql,
)
from app.services.telemetry import (
    record_operational_event,
)
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
        self._tracking_locks: weakref.WeakValueDictionary[
            str,
            asyncio.Lock,
        ] = weakref.WeakValueDictionary()
        self._refresh_locks: weakref.WeakValueDictionary[
            int,
            asyncio.Lock,
        ] = weakref.WeakValueDictionary()
        self._provider_semaphores: dict[str, asyncio.Semaphore] = {}

        self.melhor = (
            MelhorRastreioProvider(settings.http_timeout_seconds)
            if settings.melhor_rastreio_enabled
            else None
        )
        self.rastreador_pacotes = (
            RastreadorPacotesProvider(
                min(
                    settings.http_timeout_seconds,
                    15.0,
                )
            )
            if settings.direct_fallbacks_enabled
            else None
        )
        self.correios = (
            CorreiosDirectProvider(settings.http_timeout_seconds)
            if settings.direct_fallbacks_enabled
            else None
        )
        self.jadlog = (
            JadlogDirectProvider(settings.http_timeout_seconds)
            if settings.direct_fallbacks_enabled
            else None
        )
        self.total_express = (
            TotalExpressDirectProvider(settings.http_timeout_seconds)
            if settings.direct_fallbacks_enabled
            else None
        )

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

        self._providers: dict[str, Any] = {
            provider.name: provider
            for provider in (
                self.melhor,
                self.rastreador_pacotes,
                self.correios,
                self.jadlog,
                self.total_express,
                self.seventeen,
                self.ship24,
            )
            if provider is not None
        }

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

        try:
            async with session.begin_nested():
                session.add(user)
                await session.flush()
            return user
        except IntegrityError:
            # Outra requisição/réplica criou o mesmo usuário.
            existing = await session.scalar(
                select(User).where(
                    User.telegram_id
                    == telegram_id
                )
            )
            if existing is None:
                raise
            existing.username = username
            existing.first_name = (
                first_name
            )
            return existing

    async def _check_user_limit(
        self,
        session: AsyncSession,
        user_id: int,
    ) -> None:
        active_count = await session.scalar(
            select(
                func.count(Subscription.id)
            )
            .join(
                Shipment,
                Shipment.id == Subscription.shipment_id,
            )
            .where(
                Subscription.user_id == user_id,
                Subscription.is_active.is_(True),
                Shipment.status != "delivered",
            )
        )
        if (active_count or 0) >= self.settings.max_active_shipments_per_user:
            raise ValueError(
                f"Limite de {self.settings.max_active_shipments_per_user} "
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
        lock = self._tracking_locks.setdefault(
            number,
            asyncio.Lock(),
        )

        async with lock:
            return await self._add_tracking_unlocked(
                session,
                user,
                number,
                nickname=nickname,
            )

    async def _add_tracking_unlocked(
        self,
        session: AsyncSession,
        user: User,
        tracking_number: str,
        nickname: str | None = None,
    ) -> AddResult:
        number = normalize_tracking_number(tracking_number)
        shipment = await session.scalar(
            select(Shipment).where(Shipment.tracking_number == number)
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

            try:
                async with session.begin_nested():
                    session.add(
                        shipment
                    )
                    await session.flush()
            except IntegrityError:
                # Outra requisição/réplica venceu a corrida.
                shipment = await session.scalar(
                    select(Shipment).where(
                        Shipment.tracking_number
                        == number
                    )
                )
                if shipment is None:
                    raise
                created = False

            if created:
                provider_data = (
                    await self._query_best_provider(
                        session,
                        shipment,
                    )
                )
                if provider_data:
                    await self._apply_provider_data(
                        session,
                        shipment,
                        provider_data,
                    )
                else:
                    warning = (
                        "Código salvo. Ainda não encontrei movimentações "
                        "nas fontes disponíveis; vou continuar verificando "
                        "automaticamente."
                    )
                await self._schedule_next_poll(
                    session,
                    shipment,
                )
            else:
                await self.refresh_if_stale(
                    session,
                    shipment,
                )
        else:
            # Reutiliza uma consulta recente para evitar tempestade de
            # requests quando vários usuários abrem/adicionam o mesmo código.
            await self.refresh_if_stale(
                session,
                shipment,
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
            await self._check_user_limit(
                session,
                user.id,
            )
            candidate = Subscription(
                user_id=user.id,
                shipment_id=shipment.id,
                nickname=nickname,
                notify_level=(
                    self.settings
                    .default_notify_level
                ),
            )

            try:
                async with session.begin_nested():
                    session.add(
                        candidate
                    )
                    await session.flush()
                subscription = candidate
            except IntegrityError:
                # A mesma assinatura já foi criada em outra réplica.
                subscription = await session.scalar(
                    select(Subscription)
                    .options(
                        selectinload(
                            Subscription.shipment
                        )
                    )
                    .where(
                        Subscription.user_id
                        == user.id,
                        Subscription.shipment_id
                        == shipment.id,
                    )
                )
                if subscription is None:
                    raise
        else:
            subscription.is_active = True
            if nickname:
                subscription.nickname = nickname

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
        shipment = await session.get(Shipment, shipment_id)
        if not shipment:
            return None

        sub = await session.scalar(
            select(Subscription).where(
                Subscription.user_id == user.id,
                Subscription.shipment_id == shipment_id,
            )
        )
        if sub is None:
            await self._check_user_limit(session, user.id)
            sub = Subscription(
                user_id=user.id,
                shipment_id=shipment_id,
                notify_level=self.settings.default_notify_level,
                notifications_enabled=True,
                is_active=True,
            )
            session.add(sub)
        else:
            sub.is_active = True
            sub.notifications_enabled = True
            if sub.notify_level == "off":
                sub.notify_level = self.settings.default_notify_level

        await session.commit()
        return await self.get_subscription(session, user.id, sub.id)

    def _candidate_providers(self, shipment: Shipment) -> list[Any]:
        candidates: list[Any] = []

        correios_primary = bool(
            self.rastreador_pacotes
            and self.rastreador_pacotes.can_handle(
                shipment.tracking_number
            )
        )

        if correios_primary:
            candidates.append(
                self.rastreador_pacotes
            )

        if self.melhor:
            candidates.append(self.melhor)

        if (
            shipment.provider in self._providers
            and self._providers[shipment.provider]
            not in candidates
            and not (
                correios_primary
                and self._providers[shipment.provider]
                is self.correios
            )
        ):
            candidates.append(
                self._providers[
                    shipment.provider
                ]
            )

        # O proxy antigo dos Correios fica somente como fallback
        # quando a fonte fresca + Melhor Rastreio não responderem.
        if (
            self.correios
            and self.correios.can_handle(
                shipment.tracking_number
            )
            and not correios_primary
        ):
            candidates.append(
                self.correios
            )

        if (
            self.jadlog
            and self.jadlog.can_handle(
                shipment.tracking_number
            )
        ):
            candidates.append(
                self.jadlog
            )

        if (
            self.total_express
            and self.total_express.can_handle(
                shipment.tracking_number
            )
        ):
            candidates.append(
                self.total_express
            )

        for optional in (
            self.seventeen,
            self.ship24,
        ):
            if (
                optional
                and optional not in candidates
            ):
                candidates.append(
                    optional
                )

        deduped: list[Any] = []
        names: set[str] = set()

        for provider in candidates:
            if provider.name not in names:
                names.add(provider.name)
                deduped.append(
                    provider
                )

        return deduped

    @classmethod
    def _provider_freshness_key(
        cls,
        data: ProviderTracking,
    ) -> tuple[datetime, int, int]:
        latest = datetime.min.replace(
            tzinfo=timezone.utc
        )

        if data.events:
            latest_event = max(
                data.events,
                key=lambda item: (
                    cls._as_utc(item.event_at)
                    or datetime.min.replace(
                        tzinfo=timezone.utc
                    )
                ),
            )
            latest = (
                cls._as_utc(latest_event.event_at)
                or latest
            )

        # On an exact timestamp tie, prefer the result with
        # more history. For Correios, prefer the direct source
        # because it usually reflects the official feed sooner.
        direct_bonus = {
            "rastreador_pacotes": 2,
            "correios_direct": 1,
        }.get(
            data.provider,
            0,
        )
        return (
            latest,
            len(data.events),
            direct_bonus,
        )

    @staticmethod
    def _mark_provider_success(
        row: ProviderHealth,
    ) -> None:
        row.consecutive_failures = 0
        row.quarantined_until = None
        row.last_success_at = (
            datetime.now(timezone.utc)
        )
        row.last_error = None

    @staticmethod
    def _mark_provider_failure(
        row: ProviderHealth,
        error: str,
    ) -> None:
        row.consecutive_failures += 1
        row.last_failure_at = (
            datetime.now(timezone.utc)
        )
        row.last_error = (
            error
            or "erro desconhecido"
        )[:500]

        failures = (
            row.consecutive_failures
        )
        quarantine = None

        if failures >= 12:
            quarantine = timedelta(
                hours=24
            )
        elif failures >= 6:
            quarantine = timedelta(
                hours=6
            )
        elif failures >= 3:
            quarantine = timedelta(
                hours=1
            )

        if quarantine:
            row.quarantined_until = (
                datetime.now(timezone.utc)
                + quarantine
            )

    async def _query_best_provider(
        self,
        session: AsyncSession,
        shipment: Shipment,
    ) -> ProviderTracking | None:
        raw_candidates = (
            self._candidate_providers(
                shipment
            )
        )

        if not raw_candidates:
            return None

        fallback_provider = None

        if (
            self.correios
            and self.correios.can_handle(
                shipment.tracking_number
            )
            and self.correios
            not in raw_candidates
        ):
            fallback_provider = (
                self.correios
            )

        health_providers = list(
            raw_candidates
        )
        if fallback_provider:
            health_providers.append(
                fallback_provider
            )

        names = [
            provider.name
            for provider
            in health_providers
        ]

        rows = list(
            (
                await session.scalars(
                    select(
                        ProviderHealth
                    ).where(
                        ProviderHealth.provider
                        .in_(names)
                    )
                )
            ).all()
        )

        health_by_name = {
            row.provider: row
            for row in rows
        }

        for name in names:
            if name not in health_by_name:
                row = ProviderHealth(
                    provider=name
                )
                session.add(row)
                health_by_name[
                    name
                ] = row

        await session.flush()

        now = datetime.now(
            timezone.utc
        )
        candidates: list[Any] = []

        for provider in raw_candidates:
            row = health_by_name[
                provider.name
            ]
            until = self._as_utc(
                row.quarantined_until
            )

            if (
                until
                and until > now
            ):
                continue

            candidates.append(
                provider
            )

        # Libera a conexão do Postgres enquanto esperamos I/O externo.
        await session.commit()

        if not candidates:
            return None

        timeout = max(
            3.0,
            float(
                self.settings
                .provider_query_timeout_seconds
            ),
        )
        loop = asyncio.get_running_loop()

        async def fetch_candidate(
            provider: Any,
        ):
            started = loop.time()

            def elapsed_ms() -> int:
                return max(
                    0,
                    int(
                        round(
                            (
                                loop.time()
                                - started
                            )
                            * 1000
                        )
                    ),
                )

            try:
                semaphore = (
                    self._provider_semaphores
                    .setdefault(
                        provider.name,
                        asyncio.Semaphore(
                            max(
                                1,
                                self.settings
                                .provider_concurrency_per_source,
                            )
                        ),
                    )
                )

                async with semaphore:
                    data = await asyncio.wait_for(
                        self._query_provider(
                            provider,
                            shipment,
                        ),
                        timeout=timeout,
                    )

                return (
                    provider,
                    data,
                    None,
                    False,
                    elapsed_ms(),
                )
            except ProviderNotFound as exc:
                return (
                    provider,
                    None,
                    exc,
                    True,
                    elapsed_ms(),
                )
            except asyncio.TimeoutError:
                return (
                    provider,
                    None,
                    ProviderUnavailable(
                        "Tempo limite excedido na consulta."
                    ),
                    False,
                    elapsed_ms(),
                )
            except ProviderUnavailable as exc:
                return (
                    provider,
                    None,
                    exc,
                    False,
                    elapsed_ms(),
                )
            except Exception as exc:
                return (
                    provider,
                    None,
                    exc,
                    False,
                    elapsed_ms(),
                )

        results = await asyncio.gather(
            *(
                fetch_candidate(
                    provider
                )
                for provider
                in candidates
            )
        )

        available: list[
            ProviderTracking
        ] = []

        for (
            provider,
            data,
            error,
            healthy_not_found,
            duration_ms,
        ) in results:
            row = health_by_name[
                provider.name
            ]
            healthy = (
                data is not None
                or healthy_not_found
            )
            await record_operational_event(
                session,
                kind="provider_query",
                name=provider.name,
                ok=healthy,
                duration_ms=duration_ms,
                detail=(
                    "not_found"
                    if healthy_not_found
                    else (
                        type(error).__name__
                        if error is not None
                        else None
                    )
                ),
                context_name=(
                    shipment.carrier_name
                    or shipment.carrier_code
                    or "Em detecção"
                ),
            )

            if data is not None:
                self._mark_provider_success(
                    row
                )
                available.append(
                    data
                )
                continue

            if healthy_not_found:
                self._mark_provider_success(
                    row
                )
                continue

            if error is not None:
                self._mark_provider_failure(
                    row,
                    str(error),
                )

        if (
            not available
            and fallback_provider
        ):
            row = health_by_name[
                fallback_provider.name
            ]
            until = self._as_utc(
                row.quarantined_until
            )

            if not (
                until
                and until
                > datetime.now(
                    timezone.utc
                )
            ):
                (
                    provider,
                    data,
                    error,
                    healthy_not_found,
                    duration_ms,
                ) = await fetch_candidate(
                    fallback_provider
                )

                await record_operational_event(
                    session,
                    kind="provider_query",
                    name=provider.name,
                    ok=(
                        data is not None
                        or healthy_not_found
                    ),
                    duration_ms=duration_ms,
                    detail=(
                        "not_found"
                        if healthy_not_found
                        else (
                            type(error).__name__
                            if error is not None
                            else None
                        )
                    ),
                    context_name=(
                        shipment.carrier_name
                        or shipment.carrier_code
                        or "Em detecção"
                    ),
                )

                if data is not None:
                    self._mark_provider_success(
                        row
                    )
                    available.append(
                        data
                    )
                elif healthy_not_found:
                    self._mark_provider_success(
                        row
                    )
                elif error is not None:
                    self._mark_provider_failure(
                        row,
                        str(error),
                    )

        await session.flush()

        if not available:
            return None

        best = max(
            available,
            key=self._provider_freshness_key,
        )

        # Preserve ETA from another healthy source when the
        # freshest Correios feed does not provide one.
        if isinstance(
            best.raw,
            dict,
        ):
            if not best.raw.get(
                "estimatedDelivery"
            ):
                for candidate in available:
                    raw = (
                        candidate.raw
                        if isinstance(
                            candidate.raw,
                            dict,
                        )
                        else {}
                    )
                    eta = raw.get(
                        "estimatedDelivery"
                    )
                    if eta:
                        best.raw = {
                            **best.raw,
                            "estimatedDelivery": eta,
                        }
                        break

        return best

    async def _query_provider(
        self,
        provider: Any,
        shipment: Shipment,
    ) -> ProviderTracking:
        if provider is self.seventeen:
            if shipment.provider != provider.name:
                registered = await provider.register(shipment.tracking_number)
                if registered.events:
                    return registered
            return await provider.fetch(
                shipment.tracking_number,
                shipment.carrier_code,
                shipment.provider_tracking_id,
            )

        if provider is self.ship24 and shipment.provider != provider.name:
            return await provider.register(shipment.tracking_number)

        data = await provider.fetch(
            shipment.tracking_number,
            shipment.carrier_code,
            shipment.provider_tracking_id,
        )
        if not data.events and not data.status_raw:
            raise ProviderNotFound("Fonte respondeu sem eventos.")
        return data

    async def _health_row(
        self,
        session: AsyncSession,
        provider_name: str,
    ) -> ProviderHealth:
        row = await session.scalar(
            select(ProviderHealth).where(
                ProviderHealth.provider == provider_name
            )
        )
        if row is None:
            row = ProviderHealth(provider=provider_name)
            session.add(row)
            await session.flush()
        return row

    @staticmethod
    def _as_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


    @classmethod
    def _notification_events_after(
        cls,
        events: list[TrackingEvent],
        previous_last_event_at: datetime | None,
    ) -> list[TrackingEvent]:
        if not events:
            return []

        cutoff = cls._as_utc(
            previous_last_event_at
        )

        if cutoff is None:
            return sorted(
                events,
                key=lambda item: (
                    cls._as_utc(
                        item.event_at
                    )
                    or datetime.min.replace(
                        tzinfo=timezone.utc
                    )
                ),
            )

        fresh = [
            event
            for event in events
            if (
                cls._as_utc(
                    event.event_at
                )
                and cls._as_utc(
                    event.event_at
                ) > cutoff
            )
        ]

        return sorted(
            fresh,
            key=lambda item: (
                cls._as_utc(
                    item.event_at
                )
                or datetime.min.replace(
                    tzinfo=timezone.utc
                )
            ),
        )

    async def _provider_quarantined(
        self,
        session: AsyncSession,
        provider_name: str,
    ) -> bool:
        row = await self._health_row(session, provider_name)
        until = self._as_utc(row.quarantined_until)
        return bool(until and until > datetime.now(timezone.utc))

    async def _record_provider_success(
        self,
        session: AsyncSession,
        provider_name: str,
    ) -> None:
        row = await self._health_row(session, provider_name)
        row.consecutive_failures = 0
        row.quarantined_until = None
        row.last_success_at = datetime.now(timezone.utc)
        row.last_error = None
        await session.flush()

    async def _record_provider_failure(
        self,
        session: AsyncSession,
        provider_name: str,
        error: str,
    ) -> None:
        row = await self._health_row(session, provider_name)
        row.consecutive_failures += 1
        row.last_failure_at = datetime.now(timezone.utc)
        row.last_error = (error or "erro desconhecido")[:500]

        failures = row.consecutive_failures
        quarantine = None
        if failures >= 12:
            quarantine = timedelta(hours=24)
        elif failures >= 6:
            quarantine = timedelta(hours=6)
        elif failures >= 3:
            quarantine = timedelta(hours=1)

        if quarantine:
            row.quarantined_until = datetime.now(timezone.utc) + quarantine
        await session.flush()

    async def refresh_if_stale(
        self,
        session: AsyncSession,
        shipment: Shipment,
        *,
        min_age_seconds: int | None = None,
    ) -> ProviderTracking | None:
        if shipment.id is None:
            return await self.refresh_shipment(
                session,
                shipment,
            )

        lock = self._refresh_locks.setdefault(
            shipment.id,
            asyncio.Lock(),
        )

        async with lock:
            state = await session.scalar(
                select(PollingState).where(
                    PollingState.shipment_id
                    == shipment.id
                )
            )

            minimum_age = max(
                0,
                (
                    self.settings.manual_refresh_min_seconds
                    if min_age_seconds is None
                    else min_age_seconds
                ),
            )

            if (
                state
                and state.last_checked_at
                and minimum_age > 0
            ):
                last_checked = self._as_utc(
                    state.last_checked_at
                )
                if (
                    last_checked
                    and (
                        datetime.now(timezone.utc)
                        - last_checked
                    ).total_seconds()
                    < minimum_age
                ):
                    return None

            return await self.refresh_shipment(
                session,
                shipment,
            )

    async def refresh_shipment(
        self,
        session: AsyncSession,
        shipment: Shipment,
    ) -> ProviderTracking | None:
        data, _ = await self.refresh_with_events(session, shipment)
        return data

    async def refresh_with_events(
        self,
        session: AsyncSession,
        shipment: Shipment,
    ) -> tuple[ProviderTracking | None, list[TrackingEvent]]:
        previous_last_event_at = shipment.last_event_at

        provider_data = await self._query_best_provider(
            session,
            shipment,
        )
        stored_events: list[TrackingEvent] = []
        notification_events: list[TrackingEvent] = []

        if provider_data:
            stored_events = await self._apply_provider_data(
                session,
                shipment,
                provider_data,
            )
            notification_events = (
                self._notification_events_after(
                    stored_events,
                    previous_last_event_at,
                )
            )
            await self._schedule_next_poll(
                session,
                shipment,
            )
        else:
            await self._schedule_next_poll(
                session,
                shipment,
                error=(
                    "Nenhuma fonte encontrou o código "
                    "nesta rodada."
                ),
            )

        await session.commit()
        return provider_data, notification_events

    async def apply_webhook(
        self,
        session: AsyncSession,
        data: ProviderTracking,
    ) -> tuple[Shipment | None, list[TrackingEvent]]:
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

        previous_last_event_at = (
            shipment.last_event_at
        )

        stored_events = await self._apply_provider_data(
            session,
            shipment,
            data,
        )
        notification_events = (
            self._notification_events_after(
                stored_events,
                previous_last_event_at,
            )
        )

        await self._schedule_next_poll(
            session,
            shipment,
        )
        await session.commit()
        return shipment, notification_events

    @classmethod
    def _accept_provider_snapshot(
        cls,
        shipment: Shipment,
        data: ProviderTracking,
    ) -> bool:
        if not data.events:
            return shipment.last_event_at is None

        incoming = max(
            data.events,
            key=lambda item: (
                cls._as_utc(item.event_at)
                or datetime.min.replace(
                    tzinfo=timezone.utc
                )
            ),
        )
        incoming_at = cls._as_utc(
            incoming.event_at
        )
        current_at = cls._as_utc(
            shipment.last_event_at
        )

        if current_at is None:
            return True

        if incoming_at is None:
            return False

        return incoming_at >= current_at

    async def _apply_provider_data(
        self,
        session: AsyncSession,
        shipment: Shipment,
        data: ProviderTracking,
    ) -> list[TrackingEvent]:
        accept_snapshot = (
            self._accept_provider_snapshot(
                shipment,
                data,
            )
        )

        # Metadados básicos podem preencher lacunas mesmo em backfill,
        # mas não substituem um snapshot atual por um mais antigo.
        if not shipment.carrier_code:
            shipment.carrier_code = (
                data.carrier_code
                or shipment.carrier_code
            )
        if not shipment.carrier_name:
            shipment.carrier_name = (
                data.carrier_name
                or shipment.carrier_name
            )

        if accept_snapshot:
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
                # Mantém JSON válido inteiro para rota, ETA e histórico.
                shipment.extra_json = json.dumps(
                    data.raw,
                    ensure_ascii=False,
                    default=str,
                )
            except Exception:
                pass

        event_rows: list[
            tuple[Any, str]
        ] = []

        for ev in data.events:
            event_rows.append(
                (
                    ev,
                    event_hash(
                        shipment.tracking_number,
                        ev.status,
                        ev.description,
                        ev.location,
                        ev.event_at,
                    ),
                )
            )

        existing_hashes: set[str] = set()

        if event_rows:
            hashes = [
                item[1]
                for item in event_rows
            ]
            existing_hashes = set(
                (
                    await session.scalars(
                        select(
                            TrackingEvent.event_hash
                        ).where(
                            TrackingEvent.shipment_id
                            == shipment.id,
                            TrackingEvent.event_hash.in_(
                                hashes
                            ),
                        )
                    )
                ).all()
            )

        new_events: list[
            TrackingEvent
        ] = []

        for ev, h in event_rows:
            if h in existing_hashes:
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

        if data.events and accept_snapshot:
            latest = max(
                data.events,
                key=lambda item: (
                    self._as_utc(
                        item.event_at
                    )
                    or datetime.min.replace(
                        tzinfo=timezone.utc
                    )
                ),
            )
            shipment.status = (
                latest.status
            )
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
                latest.status
                != "delivered"
            )

            if (
                latest.status
                == "delivered"
            ):
                shipment.delivered_at = (
                    latest.event_at
                )

        elif (
            not data.events
            and data.status_raw
            and shipment.last_event_at
            is None
        ):
            shipment.status = (
                normalize_status(
                    data.status_raw
                )
            )

        elif (
            data.provider
            and shipment.status
            == "unknown"
            and shipment.last_event_at
            is None
        ):
            shipment.status = (
                "info_received"
            )

        return new_events

    def poll_interval_minutes(self, status: str) -> int:
        if status == "out_for_delivery":
            return self.settings.poll_out_for_delivery_minutes
        if status == "arrived_destination":
            return self.settings.poll_destination_minutes
        if status in {"picked_up", "in_transit"}:
            return self.settings.poll_transit_minutes
        if status in {
            "customs",
            "available_for_pickup",
            "delivery_failed",
            "exception",
            "returned",
        }:
            return self.settings.poll_exception_minutes
        return self.settings.poll_unknown_minutes

    async def _schedule_next_poll(
        self,
        session: AsyncSession,
        shipment: Shipment,
        error: str | None = None,
    ) -> None:
        state = await session.get(PollingState, shipment.id)
        if state is None:
            state = PollingState(shipment_id=shipment.id)
            session.add(state)

        now = datetime.now(timezone.utc)
        state.last_checked_at = now
        state.last_error = error[:500] if error else None

        if shipment.status == "delivered" or not shipment.is_active:
            state.next_check_at = None
        else:
            minutes = max(5, self.poll_interval_minutes(shipment.status))
            state.next_check_at = now + timedelta(minutes=minutes)
        await session.flush()

    async def provider_health_snapshot(
        self,
        session: AsyncSession,
    ) -> list[ProviderHealth]:
        return list(
            (
                await session.scalars(
                    select(ProviderHealth).order_by(ProviderHealth.provider)
                )
            ).all()
        )

    async def find_subscriptions(
        self,
        session: AsyncSession,
        user_id: int,
        *,
        delivered: bool | None = False,
        archived: bool | None = None,
        status: str | None = None,
        carrier: str | None = None,
        query: str | None = None,
        page: int = 0,
        page_size: int | None = None,
    ) -> tuple[list[Subscription], int]:
        page_size = page_size or self.settings.list_page_size
        conditions = [
            Subscription.user_id == user_id,
            Subscription.is_active.is_(True),
        ]

        if delivered is True:
            conditions.append(Shipment.status == "delivered")

            if archived is not None:
                cutoff = archive_cutoff(
                    self.settings.delivered_archive_after_days
                )
                reference = delivered_reference_sql()
                conditions.append(
                    reference < cutoff
                    if archived
                    else reference >= cutoff
                )
        elif delivered is False:
            conditions.append(Shipment.status != "delivered")

        if status:
            conditions.append(Shipment.status == status)
        if carrier:
            conditions.append(
                func.lower(Shipment.carrier_name) == carrier.lower()
            )
        if query:
            q = f"%{query.strip()}%"
            conditions.append(
                or_(
                    Shipment.tracking_number.ilike(q),
                    Shipment.carrier_name.ilike(q),
                    Shipment.status.ilike(q),
                    Shipment.status_raw.ilike(q),
                    Shipment.last_description.ilike(q),
                    Subscription.nickname.ilike(q),
                )
            )

        count_stmt = (
            select(func.count(Subscription.id))
            .join(Shipment)
            .where(*conditions)
        )
        total = int(await session.scalar(count_stmt) or 0)

        stmt = (
            select(Subscription)
            .options(selectinload(Subscription.shipment))
            .join(Shipment)
            .where(*conditions)
            .order_by(
                Shipment.last_event_at.desc().nullslast(),
                Subscription.id.desc(),
            )
            .offset(max(0, page) * page_size)
            .limit(page_size)
        )

        return list((await session.scalars(stmt)).all()), total

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
            select(Shipment.carrier_name)
            .join(
                Subscription,
                Subscription.shipment_id == Shipment.id,
            )
            .where(
                Subscription.user_id == user_id,
                Subscription.is_active.is_(True),
                Shipment.carrier_name.is_not(None),
            )
            .distinct()
            .order_by(Shipment.carrier_name)
        )
        return [
            str(item)
            for item in (await session.scalars(stmt)).all()
            if item
        ]

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
                    .where(TrackingEvent.shipment_id == shipment_id)
                    .order_by(TrackingEvent.event_at.desc())
                    .limit(limit)
                )
            ).all()
        )

    async def active_shipments_for_polling(
        self,
        session: AsyncSession,
    ) -> list[Shipment]:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=self.settings.poll_tracking_days)

        stmt = (
            select(Shipment)
            .outerjoin(
                PollingState,
                PollingState.shipment_id == Shipment.id,
            )
            .where(
                Shipment.is_active.is_(True),
                Shipment.status != "delivered",
                Shipment.registered_at >= cutoff,
                or_(
                    PollingState.next_check_at.is_(None),
                    PollingState.next_check_at <= now,
                ),
            )
            .order_by(
                PollingState.next_check_at
                .asc()
                .nullsfirst(),
                Shipment.last_event_at
                .asc()
                .nullsfirst(),
            )
            .limit(
                max(
                    50,
                    self.settings.poll_batch_size,
                )
            )
        )
        return list((await session.scalars(stmt)).all())
