import asyncio

from telegram.ext import Application

from app.bot.router import register_handlers
from app.config import Settings
from app.services.tracking import TrackingService


def build_telegram_app(
    settings: Settings,
    tracking_service: TrackingService,
) -> Application | None:
    if not settings.telegram_bot_token:
        return None

    update_queue: asyncio.Queue = asyncio.Queue(
        maxsize=max(
            1000,
            settings.telegram_update_queue_size,
        )
    )

    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .update_queue(update_queue)
        .concurrent_updates(
            max(
                1,
                settings.telegram_concurrent_updates,
            )
        )
        .build()
    )
    app.bot_data[
        "tracking_service"
    ] = tracking_service
    register_handlers(app)
    return app


async def set_commands(app: Application) -> None:
    # O fluxo principal é por texto e botões fixos; não exibe menu de /comandos.
    await app.bot.delete_my_commands()
