import pytest

from app.config import Settings


def test_admin_preview_flag_defaults_off():
    settings = Settings(
        telegram_bot_token="",
        database_url="sqlite+aiosqlite:///:memory:",
    )
    assert settings.admin_preview_notifications_on_startup is False
