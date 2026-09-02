import pytest

from app.api.health import InfrastructureHealthChecker


class BrokenEngine:
    def connect(self) -> None:
        raise ConnectionError("database-secret")


class BrokenRedis:
    async def ping(self) -> bool:
        raise ConnectionError("redis-secret")


@pytest.mark.asyncio
async def test_checker_converts_dependency_exceptions_to_safe_status() -> None:
    checker = InfrastructureHealthChecker(BrokenEngine(), BrokenRedis())  # type: ignore[arg-type]
    result = await checker.check()
    assert not result.ready
    assert not result.postgres
    assert not result.redis
    assert not result.pgvector
