from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from app.bot import handlers


def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", handlers.start))
    app.add_handler(CommandHandler("ajuda", handlers.help_cmd))
    app.add_handler(CommandHandler("help", handlers.help_cmd))
    app.add_handler(CommandHandler("rastrear", handlers.track_cmd))
    app.add_handler(CommandHandler("meus", handlers.my_shipments))
    app.add_handler(CommandHandler("entregues", handlers.delivered))
    app.add_handler(CommandHandler("transportadoras", handlers.carriers))
    app.add_handler(CommandHandler("config", handlers.config_cmd))
    app.add_handler(CommandHandler("status", handlers.bot_status))
    app.add_handler(CommandHandler("privacidade", handlers.privacy_cmd))
    app.add_handler(CommandHandler("cancelar", handlers.cancel_cmd))
    app.add_handler(CommandHandler("admin", handlers.admin))
    app.add_handler(CommandHandler("broadcast", handlers.broadcast))
    app.add_handler(CallbackQueryHandler(handlers.callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.text_tracking))
