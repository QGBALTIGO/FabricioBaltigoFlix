from telegram import BotCommand
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
    app = Application.builder().token(settings.telegram_bot_token).build()
    app.bot_data["tracking_service"] = tracking_service
    register_handlers(app)
    return app


async def set_commands(app: Application) -> None:
    # O fluxo principal é por texto e botões fixos; não exibe menu de /comandos.
    await app.bot.delete_my_commands()
