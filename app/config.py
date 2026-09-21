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

    admin_ids_raw: str = Field(default="", alias="ADMIN_IDS")

    # Primary free tracking source.
    melhor_rastreio_enabled: bool = True
    direct_fallbacks_enabled: bool = True

    # Optional paid/external fallbacks kept for compatibility.
    seventeen_track_token: str = ""
    seventeen_track_verify_signature: bool = True
    ship24_api_key: str = ""
    ship24_webhook_secret: str = ""
    webhook_shared_secret: str = ""

    database_url: str = "sqlite+aiosqlite:///./tracker.db"
    public_api_token: str = ""
    share_secret: str = ""

    default_notify_level: str = "important"
    max_active_shipments_per_user: int = 100
    rate_limit_per_minute: int = 20
    http_timeout_seconds: float = 30.0

    list_page_size: int = 10
    stale_after_hours: int = 72
    stale_monitor_enabled: bool = True
    stale_check_interval_minutes: int = 60

    tracking_poller_enabled: bool = True
    monitor_tick_minutes: int = 5
    poll_tracking_days: int = 30
    poll_request_spacing_seconds: float = 0.25

    poll_unknown_minutes: int = 60
    poll_transit_minutes: int = 30
    poll_destination_minutes: int = 15
    poll_out_for_delivery_minutes: int = 5
    poll_exception_minutes: int = 30

    support_url: str = ""

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
        return self.webhook_base_url.rstrip("/") + "/telegram/webhook"

    @property
    def has_tracking_provider(self) -> bool:
        return bool(
            self.melhor_rastreio_enabled
            or self.direct_fallbacks_enabled
            or self.seventeen_track_token
            or self.ship24_api_key
        )

    @property
    def effective_share_secret(self) -> str:
        return (
            self.share_secret
            or self.webhook_shared_secret
            or self.telegram_webhook_secret
            or self.telegram_bot_token
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
