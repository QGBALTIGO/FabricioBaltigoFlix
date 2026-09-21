from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings
from app.models import Base

settings = get_settings()


def normalize_async_database_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://"):]
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://"):]
    return url


database_url = normalize_async_database_url(
    settings.database_url
)

engine_kwargs = {
    "pool_pre_ping": True,
}

if database_url.startswith(
    "postgresql+asyncpg://"
):
    engine_kwargs.update(
        {
            "pool_size": max(
                5,
                settings.db_pool_size,
            ),
            "max_overflow": max(
                0,
                settings.db_max_overflow,
            ),
            "pool_timeout": max(
                5.0,
                settings.db_pool_timeout_seconds,
            ),
            "pool_recycle": 1800,
        }
    )

engine = create_async_engine(
    database_url,
    **engine_kwargs,
)

SessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all
        )

        # Sem Alembic neste projeto, garante índices novos também
        # em bancos que já possuem as tabelas.
        for statement in (
            (
                "CREATE INDEX IF NOT EXISTS "
                "ix_shipment_poll_active_registered "
                "ON shipments (is_active, status, registered_at)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS "
                "ix_shipment_last_event_at "
                "ON shipments (last_event_at)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS "
                "ix_subscription_shipment_notify "
                "ON subscriptions "
                "(shipment_id, is_active, notifications_enabled)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS "
                "ix_polling_next_check "
                "ON polling_states (next_check_at)"
            ),
            # Horários silenciosos foram removidos da experiência. Desativa
            # qualquer preferência antiga e libera alertas que ainda estejam
            # aguardando uma janela de silêncio de versões anteriores.
            (
                "UPDATE user_preferences "
                "SET quiet_hours_enabled = FALSE "
                "WHERE quiet_hours_enabled = TRUE"
            ),
            (
                "UPDATE deferred_notifications "
                "SET deliver_after = CURRENT_TIMESTAMP "
                "WHERE deliver_after > CURRENT_TIMESTAMP"
            ),
        ):
            await conn.exec_driver_sql(
                statement
            )


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
