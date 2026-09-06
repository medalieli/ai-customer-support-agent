from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import structlog
from arq import cron
from arq.connections import ArqRedis, RedisSettings
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.domain.models import WebhookEvent, WebhookStatus
from app.infrastructure.database import create_database_engine
from app.knowledge.ingestion import process_document_version
from app.observability import (
    WORKER_JOBS,
    configure_observability,
    continued_job_context,
    observe_queue_depth,
    span,
)
from app.services.webhooks import process

settings = get_settings()
configure_logging(settings.log_level)
configure_observability(settings)
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
    ctx: dict[str, Any],
    organization_id: str,
    version_id: str,
    telemetry: dict[str, str] | None = None,
) -> dict[str, object]:
    with (
        continued_job_context(telemetry),
        span("worker.job", **{"job.name": "document_ingestion", "job.id": version_id}),
    ):
        engine = create_database_engine(settings)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                result = await process_document_version(
                    session, settings, UUID(organization_id), UUID(version_id)
                )
            WORKER_JOBS.labels("document_ingestion", "completed").inc()
            return result
        except Exception:
            WORKER_JOBS.labels("document_ingestion", "failed").inc()
            raise
        finally:
            await engine.dispose()
            if redis := ctx.get("redis"):
                await observe_queue_depth(redis)


async def process_provider_webhook(
    ctx: dict[str, Any], event_id: str, telemetry: dict[str, str] | None = None
) -> str:
    with (
        continued_job_context(telemetry),
        span("webhook.process", **{"job.name": "provider_webhook", "job.id": event_id}),
    ):
        engine = create_database_engine(settings)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                result = await process(session, settings, UUID(event_id))
            WORKER_JOBS.labels("provider_webhook", "completed").inc()
            return result
        except Exception:
            WORKER_JOBS.labels("provider_webhook", "failed").inc()
            raise
        finally:
            await engine.dispose()
            if redis := ctx.get("redis"):
                await observe_queue_depth(redis)


async def recover_provider_webhooks(ctx: dict[str, Any]) -> int:
    """Re-enqueue due failures and claims abandoned by a crashed worker."""
    engine = create_database_engine(settings)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            now = datetime.now(timezone.utc)
            rows = list(
                await session.scalars(
                    select(WebhookEvent)
                    .where(
                        or_(
                            (WebhookEvent.status == WebhookStatus.FAILED)
                            & (WebhookEvent.next_attempt_at <= now),
                            (WebhookEvent.status == WebhookStatus.PROCESSING)
                            & (WebhookEvent.processing_started_at < now - timedelta(minutes=5)),
                        )
                    )
                    .limit(100)
                )
            )
            redis: ArqRedis = ctx["redis"]
            for event in rows:
                event.status = WebhookStatus.RECEIVED
                await redis.enqueue_job("process_provider_webhook", str(event.id))
            await session.commit()
            return len(rows)
    finally:
        await engine.dispose()


redis_password = settings.redis_password.get_secret_value() if settings.redis_password else None


class WorkerSettings:
    functions = [health_verification, ingest_document_version, process_provider_webhook]
    cron_jobs = [cron(recover_provider_webhooks, second={0, 30})]
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
