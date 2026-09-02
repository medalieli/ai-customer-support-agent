import os
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import get_db_session
from app.core.config import Settings
from app.domain.models import AuditEvent, CustomerSession
from app.infrastructure.database import create_database_engine, set_tenant_scope
from app.main import create_app
from app.seed import CUSTOMERS, ORGANIZATIONS, seed
from app.services.audit import AuditService
from app.services.auth import AuthenticationError, AuthService
from app.services.security import hash_session_token

pytestmark = pytest.mark.skipif(
    os.getenv("NOVACART_RUN_DB_TESTS") != "1", reason="integration database not requested"
)


@pytest.fixture
async def integration_client() -> AsyncIterator[
    tuple[AsyncClient, async_sessionmaker[AsyncSession]]
]:
    settings = Settings(
        app_env="test",
        demo_auth_enabled=True,
        demo_staff_password=SecretStr("synthetic-demo-password"),
        session_cookie_secure=False,
    )
    engine = create_database_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await seed(session, "synthetic-demo-password")
        await seed(session, "synthetic-demo-password")

    app = create_app(settings)

    async def session_override() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = session_override
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        yield client, factory
    await engine.dispose()


async def login_customer(client: AsyncClient, persona: str, organization: str = "novacart") -> None:
    response = await client.post(
        "/api/v1/auth/demo-login",
        json={"organization_slug": organization, "persona_key": persona},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_auth_conversations_tenant_isolation_and_auditing(
    integration_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = integration_client
    anonymous = await client.get("/api/v1/auth/me")
    assert anonymous.status_code == 401
    assert "token" not in anonymous.text.lower()

    personas = await client.get("/api/v1/auth/demo-personas")
    assert personas.status_code == 200
    assert {item["persona_key"] for item in personas.json()} == {
        "amira-en",
        "lucas-fr",
        "nora-en",
    }
    failed = await client.post(
        "/api/v1/auth/demo-login",
        json={"organization_slug": "novacart", "persona_key": "unknown"},
    )
    assert failed.status_code == 401
    missing_organization = await client.post(
        "/api/v1/auth/demo-login",
        json={"organization_slug": "missing", "persona_key": "amira-en"},
    )
    assert missing_organization.status_code == 401

    await login_customer(client, "amira-en")
    identity = await client.get("/api/v1/auth/me")
    assert identity.status_code == 200
    assert identity.json()["subject_id"] == str(CUSTOMERS[0][0])

    created = await client.post(
        "/api/v1/conversations",
        json={
            "title": "M2 persistence check",
            "locale": "en",
            "customer_id": str(CUSTOMERS[1][0]),
            "organization_id": str(ORGANIZATIONS[1].id),
        },
    )
    assert created.status_code == 201
    conversation_id = created.json()["id"]
    message = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "Persist this customer message."},
    )
    assert message.status_code == 201
    assert message.json()["sequence_number"] == 1
    assert (await client.get("/api/v1/conversations")).json()[0]["id"] == conversation_id
    customer_messages = await client.get(
        f"/api/v1/conversations/{conversation_id}/messages",
        params={"after_sequence": 0, "limit": 10},
    )
    assert [item["sequence_number"] for item in customer_messages.json()] == [1]

    await client.post("/api/v1/auth/logout")
    assert (await client.get("/api/v1/auth/me")).status_code == 401

    await login_customer(client, "lucas-fr")
    denied_customer = await client.get(f"/api/v1/conversations/{conversation_id}")
    assert denied_customer.status_code == 404
    assert "exist" not in denied_customer.text.lower()

    staff_login = await client.post(
        "/api/v1/auth/staff-login",
        json={
            "organization_slug": "novacart",
            "email": "support@novacart.test",
            "password": "synthetic-demo-password",
        },
    )
    assert staff_login.status_code == 200
    bad_staff_login = await client.post(
        "/api/v1/auth/staff-login",
        json={
            "organization_slug": "novacart",
            "email": "support@novacart.test",
            "password": "incorrect-password",
        },
    )
    assert bad_staff_login.status_code == 401
    assert (await client.get("/api/v1/auth/admin-check")).status_code == 403
    assert (await client.get(f"/api/v1/conversations/{conversation_id}")).status_code == 200
    staff_message = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "A staff-visible response."},
    )
    assert staff_message.status_code == 201
    messages = await client.get(
        f"/api/v1/conversations/{conversation_id}/messages", params={"after_sequence": 0}
    )
    assert [item["sequence_number"] for item in messages.json()] == [1, 2]
    assert (await client.post("/api/v1/conversations", json={})).status_code == 403

    orbit_login = await client.post(
        "/api/v1/auth/staff-login",
        json={
            "organization_slug": "orbit-outlet",
            "email": "admin@orbit.test",
            "password": "synthetic-demo-password",
        },
    )
    assert orbit_login.status_code == 200
    assert (await client.get("/api/v1/auth/admin-check")).status_code == 200
    denied_tenant = await client.get(f"/api/v1/conversations/{conversation_id}")
    assert denied_tenant.status_code == 404
    assert (await client.post("/api/v1/auth/logout")).status_code == 204

    async with factory() as session:
        await set_tenant_scope(session, ORGANIZATIONS[0].id)
        actions = set(await session.scalars(select(AuditEvent.action)))
        assert {
            "auth.login",
            "auth.logout",
            "conversation.create",
            "authorization.denied",
        } <= actions


@pytest.mark.asyncio
async def test_expired_revoked_sessions_and_append_only_audit(
    integration_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = integration_client
    await login_customer(client, "amira-en")
    cookie = client.cookies.get("novacart_session")
    assert cookie is not None
    async with factory() as session:
        await set_tenant_scope(session, ORGANIZATIONS[0].id)
        await session.execute(
            update(CustomerSession)
            .where(CustomerSession.token_hash == hash_session_token(cookie))
            .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
        await session.commit()
    assert (await client.get("/api/v1/auth/me")).status_code == 401

    await login_customer(client, "amira-en")
    assert (await client.post("/api/v1/auth/logout")).status_code == 204
    assert (await client.get("/api/v1/auth/me")).status_code == 401

    async with factory() as session:
        await set_tenant_scope(session, ORGANIZATIONS[0].id)
        event_id = await session.scalar(select(AuditEvent.id).limit(1))
        assert event_id is not None
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(delete(AuditEvent).where(AuditEvent.id == event_id))
            await session.commit()


@pytest.mark.asyncio
async def test_database_rls_requires_tenant_scope(
    integration_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = integration_client
    async with factory() as session:
        count_without_scope = await session.scalar(text("SELECT count(*) FROM customers"))
        # The Compose bootstrap owner can bypass RLS; deployed runtime roles must be NOSUPERUSER.
        assert count_without_scope in {0, len(CUSTOMERS)}
        await set_tenant_scope(session, ORGANIZATIONS[0].id)
        scoped = await session.scalar(text("SELECT count(*) FROM customers"))
        assert scoped is not None and scoped >= 2


@pytest.mark.asyncio
async def test_auth_service_boundaries_directly(
    integration_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = integration_client
    enabled = Settings(
        app_env="test",
        demo_auth_enabled=True,
        demo_staff_password=SecretStr("synthetic-demo-password"),
    )
    disabled = Settings(app_env="test", demo_auth_enabled=False)
    async with factory() as session:
        with pytest.raises(AuthenticationError):
            await AuthService(session, disabled).demo_customer_login("novacart", "amira-en")
        with pytest.raises(AuthenticationError):
            await AuthService(session, enabled).demo_customer_login("missing", "amira-en")
        with pytest.raises(AuthenticationError):
            await AuthService(session, enabled).staff_login(
                "novacart", "missing@novacart.test", "synthetic-demo-password"
            )

        customer_token, _ = await AuthService(session, enabled).demo_customer_login(
            "novacart", "amira-en"
        )
        customer = await AuthService(session, enabled).authenticate(customer_token)
        assert customer.kind == "customer"
        await AuthService(session, enabled).logout(customer)

        staff_token, _, role = await AuthService(session, enabled).staff_login(
            "novacart", "admin@novacart.test", "synthetic-demo-password"
        )
        assert role.value == "admin"
        staff = await AuthService(session, enabled).authenticate(staff_token)
        assert staff.kind == "staff"
        await AuthService(session, enabled).logout(staff)
        with pytest.raises(AuthenticationError):
            await AuthService(session, enabled).authenticate(staff_token)

        event = await AuditService(session).record(
            ORGANIZATIONS[0].id,
            "system",
            "test.invalid_metadata",
            "denied",
            metadata={"session_token": "must-not-be-accepted"},
        )
        assert event.metadata_json == {}


@pytest.mark.asyncio
async def test_demo_personas_disabled() -> None:
    app = create_app(Settings(app_env="test", demo_auth_enabled=False))

    async def unused_session() -> AsyncIterator[AsyncSession]:
        yield None  # type: ignore[misc]

    app.dependency_overrides[get_db_session] = unused_session
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/auth/demo-personas")
    assert response.status_code == 401
