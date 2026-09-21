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
    await app.bot.set_my_commands(
        [
            BotCommand("rastrear", "Cadastrar/consultar um código"),
            BotCommand("meus", "Ver pacotes ativos"),
            BotCommand("entregues", "Ver encomendas entregues"),
            BotCommand("buscar", "Pesquisar seus rastreios"),
            BotCommand("filtros", "Filtrar por status/transportadora"),
            BotCommand("relatorio", "Resumo dos seus envios"),
            BotCommand("transportadoras", "Pesquisar transportadoras"),
            BotCommand("config", "Preferências de alertas"),
            BotCommand("seguranca", "Orientações antifraude"),
            BotCommand("status", "Estado do bot"),
            BotCommand("privacidade", "Privacidade e dados"),
            BotCommand("cancelar", "Cancelar uma edição"),
            BotCommand("ajuda", "Ajuda e comandos"),
        ]
    )
