from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Rastreio Baltigo"
    app_env: str = "production"
    log_level: str = "INFO"
    port: int = 8000
    display_timezone: str = "America/Sao_Paulo"

    telegram_bot_token: str = ""
    telegram_mode: str = "polling"
    telegram_webhook_secret: str = ""
    webhook_base_url: str = ""

    admin_ids_raw: str = Field(
        default="",
        alias="ADMIN_IDS",
    )

    seventeen_track_token: str = ""
    seventeen_track_verify_signature: bool = True
    ship24_api_key: str = ""
    ship24_webhook_secret: str = ""
    webhook_shared_secret: str = ""

    database_url: str = (
        "sqlite+aiosqlite:///./tracker.db"
    )
    public_api_token: str = ""

    default_notify_level: str = "important"
    max_active_shipments_per_user: int = 100
    rate_limit_per_minute: int = 20
    http_timeout_seconds: float = 30.0

    @property
    def admin_ids(self) -> set[int]:
        out: set[int] = set()
        for value in self.admin_ids_raw.split(","):
            value = value.strip()
            if value.isdigit():
                out.add(int(value))
        return out

    @property
    def telegram_webhook_url(self) -> str:
        if not self.webhook_base_url:
            return ""
        return (
            self.webhook_base_url.rstrip("/")
            + "/telegram/webhook"
        )

    @property
    def has_tracking_provider(self) -> bool:
        return bool(
            self.seventeen_track_token
            or self.ship24_api_key
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
