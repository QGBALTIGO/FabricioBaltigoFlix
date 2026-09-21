from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import ORJSONResponse
from sqlalchemy import (
    select,
    text as sql_text,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from telegram import Update

from app.bot.factory import (
    build_telegram_app,
    set_commands,
)
from app.bot.rich import (
    close_rich_client,
)
from app.config import get_settings
from app.database import (
    SessionLocal,
    get_session,
    init_db,
)
from app.models import Shipment
from app.providers.seventeen_track import (
    SeventeenTrackProvider,
)
from app.providers.ship24 import Ship24Provider
from app.ratelimit import SlidingWindowLimiter
from app.services.monitor import monitoring_loop
from app.services.notifier import (
    notify_new_events,
)
from app.services.tracking import TrackingService
from app.status import status_label
from app.utils import (
    humanize_age,
    is_stale,
    is_valid_tracking_number,
    normalize_tracking_number,
)

settings = get_settings()

logging.basicConfig(
    level=getattr(
        logging,
        settings.log_level.upper(),
        logging.INFO,
    ),
    format=(
        "%(asctime)s %(levelname)s "
        "%(name)s: %(message)s"
    ),
)

# Evita que URLs da Bot API (que contêm o token) apareçam nos logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

log = logging.getLogger(__name__)

tracking_service = TrackingService(
    settings
)
telegram_app = build_telegram_app(
    settings,
    tracking_service,
)
api_limiter = SlidingWindowLimiter(
    settings.rate_limit_per_minute
)
background_tasks: list[
    asyncio.Task
] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    if telegram_app:
        await telegram_app.initialize()
        await telegram_app.start()
        await set_commands(telegram_app)

        if (
            settings.telegram_mode.lower()
            == "webhook"
        ):
            if not settings.telegram_webhook_url:
                raise RuntimeError(
                    "WEBHOOK_BASE_URL é "
                    "obrigatório no modo webhook."
                )

            if (
                settings.app_env.lower()
                == "production"
                and not settings.telegram_webhook_secret
            ):
                raise RuntimeError(
                    "TELEGRAM_WEBHOOK_SECRET é obrigatório "
                    "em produção no modo webhook."
                )

            await telegram_app.bot.set_webhook(
                url=(
                    settings
                    .telegram_webhook_url
                ),
                secret_token=(
                    settings
                    .telegram_webhook_secret
                    or None
                ),
                allowed_updates=(
                    Update.ALL_TYPES
                ),
            )

        else:
            if telegram_app.updater:
                await (
                    telegram_app
                    .updater
                    .start_polling(
                        allowed_updates=(
                            Update.ALL_TYPES
                        ),
                        drop_pending_updates=(
                            False
                        ),
                    )
                )

        if (
            settings.stale_monitor_enabled
            or settings.tracking_poller_enabled
        ):
            background_tasks.append(
                asyncio.create_task(
                    monitoring_loop(
                        telegram_app.bot,
                        tracking_service,
                        settings,
                    ),
                    name="tracking-monitor",
                )
            )

    yield

    for task in background_tasks:
        task.cancel()

    for task in background_tasks:
        with suppress(
            asyncio.CancelledError
        ):
            await task

    background_tasks.clear()

    if telegram_app:
        if (
            telegram_app.updater
            and telegram_app
            .updater.running
        ):
            await (
                telegram_app
                .updater.stop()
            )

        await telegram_app.stop()
        await telegram_app.shutdown()

    await close_rich_client()


app = FastAPI(
    title=settings.app_name,
    version="1.3.0",
    default_response_class=(
        ORJSONResponse
    ),
    lifespan=lifespan,
    docs_url=(
        None
        if settings.app_env.lower()
        == "production"
        else "/docs"
    ),
    redoc_url=(
        None
        if settings.app_env.lower()
        == "production"
        else "/redoc"
    ),
    openapi_url=(
        None
        if settings.app_env.lower()
        == "production"
        else "/openapi.json"
    ),
)


@app.get("/")
async def root():
    return {
        "name": settings.app_name,
        "version": "1.3.0",
        "status": "online",
        "docs": (
            None
            if settings.app_env.lower()
            == "production"
            else "/docs"
        ),
        "telegram": bool(
            settings.telegram_bot_token
        ),
        "providers": {
            "melhor_rastreio": settings.melhor_rastreio_enabled,
            "direct_fallbacks": settings.direct_fallbacks_enabled,
            "17track": bool(settings.seventeen_track_token),
            "ship24": bool(settings.ship24_api_key),
        },
        "monitoring": {
            "stale_alerts": settings.stale_monitor_enabled,
            "smart_poller": settings.tracking_poller_enabled,
            "monitor_tick_minutes": settings.monitor_tick_minutes,
            "poll_tracking_days": settings.poll_tracking_days,
        },
    }


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/ready")
async def ready():
    try:
        async with SessionLocal() as session:
            await session.execute(
                sql_text("SELECT 1")
            )
    except Exception as exc:
        log.error(
            "Readiness do banco falhou: %s",
            type(exc).__name__,
        )
        raise HTTPException(
            503,
            "Banco indisponível",
        ) from exc

    return {"ok": True}


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token:
        str | None = Header(
            default=None
        ),
):
    if not telegram_app:
        raise HTTPException(
            503,
            "Telegram não configurado",
        )

    if (
        settings.telegram_mode.lower()
        != "webhook"
    ):
        raise HTTPException(
            404,
            "Telegram webhook desativado",
        )

    if (
        settings.telegram_webhook_secret
        and (
            x_telegram_bot_api_secret_token
            != settings
            .telegram_webhook_secret
        )
    ):
        raise HTTPException(
            403,
            "Webhook inválido",
        )

    payload = await request.json()
    update = Update.de_json(
        payload,
        telegram_app.bot,
    )

    try:
        telegram_app.update_queue.put_nowait(
            update
        )
    except asyncio.QueueFull:
        log.warning(
            "Fila de updates do Telegram cheia."
        )
        raise HTTPException(
            503,
            "Fila temporariamente cheia",
        )

    return {"ok": True}


def _shared_secret_ok(
    secret: str | None,
) -> bool:
    return bool(
        settings.webhook_shared_secret
        and secret
        and hmac.compare_digest(
            secret,
            settings.webhook_shared_secret,
        )
    )


@app.post("/webhooks/17track")
async def webhook_17track(
    request: Request,
    secret: str | None = Query(
        default=None
    ),
    sign: str | None = Header(
        default=None
    ),
):
    raw_body = await request.body()

    if (
        not settings.seventeen_track_token
        and not settings.webhook_shared_secret
    ):
        raise HTTPException(
            404,
            "Webhook 17TRACK desativado",
        )

    shared_ok = _shared_secret_ok(
        secret
    )
    signature_ok = False

    if (
        settings.seventeen_track_token
        and settings
        .seventeen_track_verify_signature
        and sign
    ):
        expected = hashlib.sha256(
            raw_body
            .decode("utf-8")
            .encode("utf-8")
            + b"/"
            + settings
            .seventeen_track_token
            .encode("utf-8")
        ).hexdigest()

        signature_ok = hmac.compare_digest(
            sign.lower(),
            expected.lower(),
        )

    if not (
        shared_ok
        or signature_ok
    ):
        raise HTTPException(
            403,
            "Webhook 17TRACK não autenticado",
        )

    payload = json.loads(
        raw_body
    )

    if (
        payload.get("event")
        and payload.get("event")
        != "TRACKING_UPDATED"
    ):
        return {
            "ok": True,
            "ignored": payload.get(
                "event"
            ),
        }

    parsed = (
        SeventeenTrackProvider
        .parse_webhook(payload)
    )

    updated = 0
    notifications = 0

    async with SessionLocal() as session:
        for item in parsed:
            shipment, new_events = (
                await tracking_service
                .apply_webhook(
                    session,
                    item,
                )
            )

            if shipment:
                updated += 1

                if (
                    telegram_app
                    and new_events
                ):
                    notifications += (
                        await notify_new_events(
                            session,
                            telegram_app.bot,
                            shipment,
                            new_events,
                        )
                    )

    return {
        "ok": True,
        "updated": updated,
        "notifications": notifications,
    }


@app.post("/webhooks/ship24")
async def webhook_ship24(
    request: Request,
    authorization: str | None = Header(
        default=None
    ),
    secret: str | None = Query(
        default=None
    ),
):
    if (
        not settings.ship24_webhook_secret
        and not settings.webhook_shared_secret
    ):
        raise HTTPException(
            404,
            "Webhook Ship24 desativado",
        )

    shared_ok = _shared_secret_ok(
        secret
    )
    bearer_ok = bool(
        settings.ship24_webhook_secret
        and authorization
        and hmac.compare_digest(
            authorization,
            (
                "Bearer "
                + settings
                .ship24_webhook_secret
            ),
        )
    )

    if not (
        shared_ok
        or bearer_ok
    ):
        raise HTTPException(
            403,
            "Webhook Ship24 não autenticado",
        )

    payload = await request.json()

    item = (
        Ship24Provider
        .parse_webhook(payload)
    )

    async with SessionLocal() as session:
        shipment, new_events = (
            await tracking_service
            .apply_webhook(
                session,
                item,
            )
        )

        notifications = 0

        if (
            shipment
            and telegram_app
            and new_events
        ):
            notifications = (
                await notify_new_events(
                    session,
                    telegram_app.bot,
                    shipment,
                    new_events,
                )
            )

    return {
        "ok": True,
        "updated": bool(shipment),
        "notifications": notifications,
    }


def _require_api_token(
    authorization: str | None,
):
    if not settings.public_api_token:
        raise HTTPException(
            503,
            "API pública desativada",
        )

    if authorization != (
        "Bearer "
        + settings.public_api_token
    ):
        raise HTTPException(
            401,
            "Token inválido",
        )


@app.get(
    "/api/v1/track/{tracking_number}"
)
async def api_track(
    tracking_number: str,
    request: Request,
    authorization: str | None = Header(
        default=None
    ),
    session: AsyncSession = Depends(
        get_session
    ),
):
    _require_api_token(
        authorization
    )

    client = (
        request.client.host
        if request.client
        else "unknown"
    )

    if not api_limiter.allow(client):
        raise HTTPException(
            429,
            "Limite de consultas excedido",
        )

    number = (
        normalize_tracking_number(
            tracking_number
        )
    )

    if not is_valid_tracking_number(
        number
    ):
        raise HTTPException(
            422,
            "Código inválido",
        )

    shipment = await session.scalar(
        select(Shipment)
        .options(
            selectinload(
                Shipment.events
            )
        )
        .where(
            Shipment.tracking_number
            == number
        )
    )

    if not shipment:
        raise HTTPException(
            404,
            (
                "Código ainda não "
                "cadastrado no bot"
            ),
        )

    events = sorted(
        shipment.events,
        key=lambda x: x.event_at,
        reverse=True,
    )[:20]

    reference = (
        shipment.last_event_at
        or shipment.registered_at
    )

    return {
        "tracking_number": (
            shipment.tracking_number
        ),
        "provider": shipment.provider,
        "carrier": {
            "code": shipment.carrier_code,
            "name": shipment.carrier_name,
        },
        "status": shipment.status,
        "status_label": status_label(
            shipment.status
        ),
        "last_update": (
            shipment.last_event_at
        ),
        "last_update_human": (
            humanize_age(reference)
        ),
        "stale": (
            shipment.status
            != "delivered"
            and is_stale(
                reference,
                settings
                .stale_after_hours,
            )
        ),
        "last_description": (
            shipment.last_description
        ),
        "last_location": (
            shipment.last_location
        ),
        "events": [
            {
                "status": e.status,
                "status_label": (
                    status_label(
                        e.status
                    )
                ),
                "description": (
                    e.description
                ),
                "location": e.location,
                "event_at": e.event_at,
            }
            for e in events
        ],
    }
