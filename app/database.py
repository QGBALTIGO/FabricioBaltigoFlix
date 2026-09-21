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


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
