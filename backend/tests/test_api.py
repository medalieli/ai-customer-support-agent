from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.health import DependencyStatus, get_health_checker
from app.core.config import Settings
from app.main import create_app


class StubHealthChecker:
    def __init__(self, status: DependencyStatus) -> None:
        self.status = status

    async def check(self) -> DependencyStatus:
        return self.status


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = create_app(Settings(app_env="test"))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


@pytest.mark.asyncio
async def test_root_response(client: AsyncClient) -> None:
    response = await client.get("/")
    assert response.status_code == 200
    assert response.json() == {
        "name": "NovaCart Support API",
        "status": "foundation_ready",
        "docs": "/docs",
    }


@pytest.mark.asyncio
async def test_liveness_response(client: AsyncClient) -> None:
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


@pytest.mark.asyncio
async def test_versioned_api_response(client: AsyncClient) -> None:
    response = await client.get("/api/v1")
    assert response.status_code == 200
    assert response.json() == {"name": "NovaCart Support API", "version": "v1"}


@pytest.mark.asyncio
async def test_readiness_success() -> None:
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_health_checker] = lambda: StubHealthChecker(
        DependencyStatus(postgres=True, redis=True, pgvector=True)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["checks"] == {
        "postgres": "up",
        "redis": "up",
        "pgvector": "available",
    }


@pytest.mark.asyncio
async def test_readiness_failure_is_safe() -> None:
    secret = "do-not-leak-password"
    app = create_app(Settings(app_env="test", postgres_password=secret))
    app.dependency_overrides[get_health_checker] = lambda: StubHealthChecker(
        DependencyStatus(postgres=False, redis=False, pgvector=False)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert secret not in response.text
    assert "postgresql" not in response.text
