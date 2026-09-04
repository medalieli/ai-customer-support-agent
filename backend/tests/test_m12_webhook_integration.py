import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.domain.models import (
    ProviderConnection,
    ProviderProjection,
    WebhookConversationEffect,
    WebhookEvent,
    WebhookStatus,
)
from app.infrastructure.database import create_database_engine, set_tenant_scope
from app.seed import ORGANIZATIONS, seed
from app.services.webhooks import WebhookRejected, accept, encrypt, process

pytestmark = pytest.mark.skipif(
    os.getenv("NOVACART_RUN_DB_TESTS") != "1", reason="database integration disabled"
)


@pytest.fixture
async def database() -> tuple[AsyncSession, Settings, ProviderConnection]:
    settings = Settings(app_env="test")
    engine = create_database_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await seed(session, "synthetic-demo-password")
        await set_tenant_scope(session, ORGANIZATIONS[0].id)
        await session.execute(delete(WebhookConversationEffect))
        await session.execute(delete(WebhookEvent))
        await session.execute(delete(ProviderProjection))
        connection = await session.scalar(
            select(ProviderConnection).where(
                ProviderConnection.organization_id == ORGANIZATIONS[0].id,
                ProviderConnection.provider == "mock_commerce",
            )
        )
        assert connection
        connection.encrypted_previous_secret = encrypt(settings, b"rotated-old-secret")
        await session.commit()
        yield session, settings, connection
    await engine.dispose()


def signed(raw: bytes, secret: bytes, event_id: str, topic: str) -> dict[str, str]:
    return {
        "content-type": "application/json",
        "x-mock-event-id": event_id,
        "x-mock-topic": topic,
        "x-mock-timestamp": str(int(datetime.now(timezone.utc).timestamp())),
        "x-mock-signature": base64.b64encode(
            hmac.new(secret, raw, hashlib.sha256).digest()
        ).decode(),
    }


@pytest.mark.asyncio
async def test_accept_duplicate_conflict_rotation_and_validation(
    database: tuple[AsyncSession, Settings, ProviderConnection],
) -> None:
    session, settings, connection = database
    endpoint_key = connection.endpoint_key
    raw = json.dumps({"id": "order-1", "status": "shipped", "tenant_id": "spoof"}).encode()
    headers = signed(raw, b"rotated-old-secret", "evt-1", "order.updated")
    event, outcome = await accept(
        session,
        settings,
        "mock_commerce",
        endpoint_key,
        raw,
        headers,
        "POST",
        "http://test/webhook",
    )
    assert outcome == "accepted" and event.organization_id == ORGANIZATIONS[0].id
    duplicate, outcome = await accept(
        session,
        settings,
        "mock_commerce",
        endpoint_key,
        raw,
        headers,
        "POST",
        "http://test/webhook",
    )
    assert outcome == "duplicate" and duplicate.id == event.id
    changed = b'{"id":"order-1","status":"cancelled"}'
    _, outcome = await accept(
        session,
        settings,
        "mock_commerce",
        endpoint_key,
        changed,
        signed(changed, b"rotated-old-secret", "evt-1", "order.updated"),
        "POST",
        "http://test/webhook",
    )
    assert outcome == "conflict"
    with pytest.raises(WebhookRejected, match="invalid_signature"):
        await accept(
            session,
            settings,
            "mock_commerce",
            endpoint_key,
            raw,
            {**headers, "x-mock-signature": "bad"},
            "POST",
            "http://test/webhook",
        )
    with pytest.raises(WebhookRejected, match="unknown_connection"):
        await accept(
            session,
            settings,
            "mock_commerce",
            "unknown",
            raw,
            headers,
            "POST",
            "http://test/webhook",
        )


@pytest.mark.asyncio
async def test_processing_ordering_retry_and_dead_letter(
    database: tuple[AsyncSession, Settings, ProviderConnection],
) -> None:
    session, settings, connection = database
    endpoint_key = connection.endpoint_key
    now = datetime.now(timezone.utc)
    newer = json.dumps(
        {"id": "order-2", "status": "delivered", "occurred_at": now.isoformat()}
    ).encode()
    event, _ = await accept(
        session,
        settings,
        "mock_commerce",
        endpoint_key,
        newer,
        signed(
            newer,
            settings.mock_commerce_webhook_secret.get_secret_value().encode(),
            "evt-new",
            "order.updated",
        ),
        "POST",
        "http://test/webhook",
    )
    assert await process(session, settings, event.id) == "unassociated"
    older_time = now - timedelta(hours=1)
    older = json.dumps(
        {"id": "order-2", "status": "shipped", "occurred_at": older_time.isoformat()}
    ).encode()
    stale, _ = await accept(
        session,
        settings,
        "mock_commerce",
        endpoint_key,
        older,
        signed(
            older,
            settings.mock_commerce_webhook_secret.get_secret_value().encode(),
            "evt-old",
            "order.updated",
        ),
        "POST",
        "http://test/webhook",
    )
    assert await process(session, settings, stale.id) == "unassociated"
    failing = json.dumps({"id": "order-3"}).encode()
    failed, _ = await accept(
        session,
        settings,
        "mock_commerce",
        endpoint_key,
        failing,
        signed(
            failing,
            settings.mock_commerce_webhook_secret.get_secret_value().encode(),
            "evt-fail",
            "order.updated",
        ),
        "POST",
        "http://test/webhook",
    )
    assert await process(session, settings, failed.id, fail="temporary") == "failed"
    for _ in range(settings.webhook_max_attempts - 1):
        await process(session, settings, failed.id, fail="permanent")
    await session.refresh(failed)
    assert failed.status == WebhookStatus.DEAD_LETTER
    failed.status = WebhookStatus.RECEIVED
    await session.commit()
    assert await process(session, settings, failed.id) == "unassociated"
