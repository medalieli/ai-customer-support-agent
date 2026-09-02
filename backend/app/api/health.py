from dataclasses import dataclass
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

router = APIRouter(tags=["health"])


@dataclass(frozen=True)
class DependencyStatus:
    postgres: bool
    redis: bool
    pgvector: bool

    @property
    def ready(self) -> bool:
        return self.postgres and self.redis and self.pgvector


class HealthChecker(Protocol):
    async def check(self) -> DependencyStatus: ...


class InfrastructureHealthChecker:
    def __init__(self, engine: AsyncEngine, redis: Redis) -> None:
        self._engine = engine
        self._redis = redis

    async def check(self) -> DependencyStatus:
        postgres_ok = False
        vector_ok = False
        redis_ok = False
        try:
            async with self._engine.connect() as connection:
                postgres_ok = bool(await connection.scalar(text("SELECT true")))
                vector_ok = bool(
                    await connection.scalar(
                        text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
                    )
                )
        except Exception:
            pass
        try:
            redis_ok = bool(await self._redis.ping())
        except Exception:
            pass
        return DependencyStatus(postgres=postgres_ok, redis=redis_ok, pgvector=vector_ok)


def get_health_checker(request: Request) -> HealthChecker:
    return InfrastructureHealthChecker(request.app.state.db_engine, request.app.state.redis)


@router.get("/health/live", summary="Process liveness")
async def liveness() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/health/ready", summary="Required dependency readiness")
async def readiness(
    checker: Annotated[HealthChecker, Depends(get_health_checker)],
) -> JSONResponse:
    result = await checker.check()
    payload = {
        "status": "ready" if result.ready else "not_ready",
        "checks": {
            "postgres": "up" if result.postgres else "down",
            "redis": "up" if result.redis else "down",
            "pgvector": "available" if result.pgvector else "unavailable",
        },
    }
    return JSONResponse(
        content=payload,
        status_code=status.HTTP_200_OK if result.ready else status.HTTP_503_SERVICE_UNAVAILABLE,
    )
