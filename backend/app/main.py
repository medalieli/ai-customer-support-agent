from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.errors import register_error_handlers
from app.api.health import router as health_router
from app.api.v1.router import router as v1_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.infrastructure.database import close_database_engine, create_database_engine
from app.infrastructure.redis import close_redis_client, create_job_queue, create_redis_client


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.db_engine = create_database_engine(resolved_settings)
        app.state.db_session_factory = async_sessionmaker(
            app.state.db_engine, expire_on_commit=False
        )
        app.state.redis = create_redis_client(resolved_settings)
        app.state.job_queue = await create_job_queue(resolved_settings)
        checkpoint_url = resolved_settings.database_url.replace("postgresql+asyncpg", "postgresql")
        checkpoint_context = AsyncPostgresSaver.from_conn_string(checkpoint_url)
        app.state.agent_checkpointer = await checkpoint_context.__aenter__()
        await app.state.agent_checkpointer.setup()
        yield
        await checkpoint_context.__aexit__(None, None, None)
        await app.state.job_queue.aclose()
        await close_redis_client(app.state.redis)
        await close_database_engine(app.state.db_engine)

    app = FastAPI(
        title="NovaCart Support API",
        summary="Infrastructure foundation for NovaCart customer support",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    if resolved_settings.agent_provider == "deterministic":
        from app.agent.deterministic import (
            DeterministicAnswerModel,
            DeterministicHandoffSummaryModel,
            DeterministicLeadExtractor,
            DeterministicTriageModel,
        )

        app.state.agent_triage_factory = DeterministicTriageModel
        app.state.agent_answer_factory = DeterministicAnswerModel
        app.state.agent_handoff_summary_factory = DeterministicHandoffSummaryModel
        app.state.agent_lead_extractor_factory = DeterministicLeadExtractor
    register_error_handlers(app)

    @app.middleware("http")
    async def browser_csrf(request: Request, call_next):  # type: ignore[no-untyped-def]
        origin = request.headers.get("origin")
        if (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and origin
            and origin == str(resolved_settings.frontend_url).rstrip("/")
            and request.headers.get("x-csrf-token") != "novacart-browser-v1"
        ):
            return JSONResponse(
                status_code=403,
                content={"error": {"code": "csrf_rejected", "message": "Request rejected"}},
            )
        return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(resolved_settings.frontend_url).rstrip("/")],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=[
            "Accept",
            "Content-Type",
            "Idempotency-Key",
            "Last-Event-ID",
            "X-CSRF-Token",
        ],
    )
    app.include_router(health_router)
    app.include_router(v1_router)

    @app.get("/", tags=["metadata"], summary="Service metadata")
    async def root() -> dict[str, str]:
        return {
            "name": "NovaCart Support API",
            "status": "foundation_ready",
            "docs": "/docs",
        }

    return app


app = create_app()
