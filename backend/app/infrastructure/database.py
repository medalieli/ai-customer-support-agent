from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings


def create_database_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={"timeout": settings.postgres_connect_timeout_seconds},
    )


async def close_database_engine(engine: AsyncEngine) -> None:
    await engine.dispose()
