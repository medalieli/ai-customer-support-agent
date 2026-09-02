from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.infrastructure.database import close_database_engine, create_database_engine
from app.infrastructure.redis import close_redis_client, create_redis_client


def test_database_engine_uses_validated_settings() -> None:
    engine = create_database_engine(
        Settings(app_env="test", postgres_password="")  # type: ignore[arg-type]
    )
    assert engine.url.drivername == "postgresql+asyncpg"
    assert engine.url.password == ""


@pytest.mark.asyncio
async def test_infrastructure_clients_close_cleanly() -> None:
    engine = create_database_engine(Settings(app_env="test"))
    await close_database_engine(engine)

    client = create_redis_client(Settings(app_env="test"))
    client.aclose = AsyncMock()  # type: ignore[method-assign]
    await close_redis_client(client)
    client.aclose.assert_awaited_once()
