from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app import prepare_database
from app.core.config import Settings
from app.main import create_app


def secure_production_settings() -> Settings:
    return Settings(
        app_env="production",
        api_url="https://support.example.test",
        frontend_url="https://support.example.test",
        docs_enabled=False,
        metrics_enabled=False,
        demo_auth_enabled=False,
        postgres_user="novacart_runtime",
        postgres_password=SecretStr("runtime-database-password-32-bytes"),
        redis_password=SecretStr("redis-production-password-32-bytes"),
        session_cookie_secure=True,
        embedding_provider="openai",
        reranker_provider="cross_encoder",
        openai_api_key=SecretStr("synthetic-openai-key"),
        action_secret=SecretStr("production-action-secret-with-32-bytes"),
        mock_commerce_internal_api_key=SecretStr("production-commerce-key-32-bytes"),
        mock_crm_internal_api_key=SecretStr("production-crm-key-32-bytes"),
        mock_commerce_webhook_secret=SecretStr("unique-commerce-webhook-secret-32-bytes"),
        mock_crm_webhook_secret=SecretStr("unique-crm-webhook-secret-more-than-32-bytes"),
    )


@pytest.fixture
async def production_client() -> AsyncIterator[AsyncClient]:
    app = create_app(secure_production_settings())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        yield client


@pytest.mark.asyncio
async def test_production_hides_docs_metrics_and_adds_security_headers(
    production_client: AsyncClient,
) -> None:
    assert (await production_client.get("/docs")).status_code == 404
    assert (await production_client.get("/openapi.json")).status_code == 404
    assert (await production_client.get("/metrics")).status_code == 404
    response = await production_client.get("/")
    assert response.json()["docs"] == "disabled"
    assert response.headers["strict-transport-security"].startswith("max-age=31536000")
    assert (
        response.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    )
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_production_csrf_and_cors_fail_closed(production_client: AsyncClient) -> None:
    rejected = await production_client.post(
        "/api/v1/auth/staff-login",
        headers={"Origin": "https://support.example.test"},
        json={"organization_slug": "novacart", "email": "x@example.test", "password": "password1"},
    )
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "csrf_rejected"
    preflight = await production_client.options(
        "/api/v1/auth/staff-login",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert preflight.status_code == 400
    assert "access-control-allow-origin" not in preflight.headers


@pytest.mark.asyncio
async def test_prepare_database_runs_langgraph_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class Saver:
        async def setup(self) -> None:
            calls.append("setup")

    class Context:
        async def __aenter__(self) -> Saver:
            calls.append("enter")
            return Saver()

        async def __aexit__(self, *args: object) -> None:
            del args
            calls.append("exit")

    class Factory:
        @staticmethod
        def from_conn_string(url: str) -> Context:
            assert url.startswith("postgresql://")
            return Context()

    monkeypatch.setattr(prepare_database, "AsyncPostgresSaver", Factory)
    monkeypatch.setattr(prepare_database, "get_settings", secure_production_settings)
    await prepare_database.prepare()
    assert calls == ["enter", "setup", "exit"]
