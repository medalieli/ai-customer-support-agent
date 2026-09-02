from typing import Any
from uuid import UUID

import structlog
from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.infrastructure.database import create_database_engine
from app.knowledge.ingestion import process_document_version

settings = get_settings()
configure_logging(settings.log_level)
logger = structlog.get_logger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    await logger.ainfo("worker_started", environment=settings.app_env)


async def shutdown(ctx: dict[str, Any]) -> None:
    await logger.ainfo("worker_stopped")


async def health_verification(ctx: dict[str, Any], probe: str = "ready") -> dict[str, str]:
    """Minimal M1-only task proving job receipt and execution."""
    await logger.ainfo("health_verification_executed")
    return {"status": "ok", "probe": probe}


async def ingest_document_version(
    ctx: dict[str, Any], organization_id: str, version_id: str
) -> dict[str, object]:
    engine = create_database_engine(settings)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            return await process_document_version(
                session, settings, UUID(organization_id), UUID(version_id)
            )
    finally:
        await engine.dispose()


redis_password = settings.redis_password.get_secret_value() if settings.redis_password else None


class WorkerSettings:
    functions = [health_verification, ingest_document_version]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings(
        host=settings.redis_host,
        port=settings.redis_port,
        database=settings.redis_db,
        password=redis_password,
        conn_timeout=int(settings.redis_connect_timeout_seconds),
    )
    health_check_interval = 10
    health_check_key = "novacart:worker:health"
