from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from app.bot import handlers, smart


def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", handlers.start))
    app.add_handler(CommandHandler("ajuda", handlers.help_cmd))
    app.add_handler(CommandHandler("help", handlers.help_cmd))
    app.add_handler(CommandHandler("rastrear", handlers.track_cmd))
    app.add_handler(CommandHandler("meus", handlers.my_shipments))
    app.add_handler(CommandHandler("hoje", smart.today_cmd))
    app.add_handler(CommandHandler("entregues", handlers.delivered))
    app.add_handler(CommandHandler("relatorio", handlers.report_cmd))
    app.add_handler(CommandHandler("config", handlers.config_cmd))
    app.add_handler(
        CommandHandler("privacidade", handlers.privacy_cmd)
    )
    app.add_handler(CommandHandler("cancelar", handlers.cancel_cmd))
    app.add_handler(CommandHandler("admin", handlers.admin))
    app.add_handler(
        CommandHandler("broadcast", handlers.broadcast)
    )
    app.add_handler(
        CallbackQueryHandler(
            smart.smart_callback,
            pattern=(
                r"^(?:today:|todaylist:|smartadd:|smartcancel:|"
                r"alertmenu:|alerttoggle:|quietpreset:|assist:)"
            ),
        )
    )
    app.add_handler(CallbackQueryHandler(handlers.callback))
    app.add_handler(
        MessageHandler(
            filters.PHOTO | filters.Document.IMAGE,
            smart.photo_tracking,
        )
    )
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handlers.text_tracking,
        )
    )
