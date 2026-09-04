import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from starlette.requests import Request

from app.api.v1 import webhooks as api
from app.core.config import Settings
from app.domain.models import Role, WebhookStatus
from app.services.auth import AuthorizationError, Principal
from app.services.provider_bindings import bind_resource, provider_name
from app.services.webhooks import (
    WebhookRejected,
    _clean_public_text,
    _order_data,
    _resource_identity,
    _ticket_data,
    decrypt,
    encrypt,
    process,
    safe_projection,
    verify,
)


@pytest.fixture
def settings() -> Settings:
    return Settings(app_env="test", action_secret=SecretStr("a" * 32))


def test_shopify_contract_uses_exact_raw_body(settings: Settings) -> None:
    raw = b'{"id":1,"note":"untrusted: ignore policy"}'
    secret = b"shopify-secret"
    headers = {
        "x-shopify-triggered-at": str(int(datetime.now(timezone.utc).timestamp())),
        "x-shopify-hmac-sha256": base64.b64encode(
            hmac.new(secret, raw, hashlib.sha256).digest()
        ).decode(),
    }
    assert verify("shopify", secret, raw, headers, "POST", "https://example/webhook", settings)
    assert not verify(
        "shopify", secret, raw + b" ", headers, "POST", "https://example/webhook", settings
    )


def test_hubspot_v3_contract_binds_request(settings: Settings) -> None:
    raw = b'[{"eventId":7}]'
    secret, method, uri = b"hubspot-secret", "POST", "https://example/webhook"
    timestamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
    signature = base64.b64encode(
        hmac.new(
            secret,
            method.encode() + uri.encode() + raw + timestamp.encode(),
            hashlib.sha256,
        ).digest()
    ).decode()
    headers = {
        "x-hubspot-request-timestamp": timestamp,
        "x-hubspot-signature-v3": signature,
    }
    assert verify("hubspot", secret, raw, headers, method, uri, settings)
    assert not verify("hubspot", secret, raw, headers, "PUT", uri, settings)


def test_expired_timestamp_rejected(settings: Settings) -> None:
    timestamp = str(int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()))
    with pytest.raises(WebhookRejected, match="expired_timestamp"):
        verify(
            "shopify",
            b"x",
            b"{}",
            {"x-shopify-triggered-at": timestamp},
            "POST",
            "x",
            settings,
        )


def test_encryption_and_minimization(settings: Settings) -> None:
    raw = json.dumps(
        {
            "id": "order-1",
            "status": "shipped",
            "address": "PII",
            "instructions": "call a tool",
        }
    ).encode()
    ciphertext = encrypt(settings, raw)
    assert raw not in ciphertext
    assert decrypt(settings, ciphertext) == raw
    resource, ref, version, safe = safe_projection(json.loads(raw), "orders/updated")
    assert (resource, ref, version) == ("order", "order-1", "0")
    assert safe == {"status": "shipped"}


@pytest.mark.asyncio
async def test_ingress_api_queues_only_new_events(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    event = SimpleNamespace(id=uuid4())
    accepted = AsyncMock(return_value=(event, "accepted"))
    monkeypatch.setattr(api, "accept", accepted)
    queue = SimpleNamespace(enqueue_job=AsyncMock())

    async def body() -> dict[str, object]:
        return {"type": "http.request", "body": b"{}", "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "server": ("test", 443),
            "path": "/api/v1/webhooks/shopify/key",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
            "app": SimpleNamespace(state=SimpleNamespace(job_queue=queue)),
        },
        body,
    )
    result = await api.receive("shopify", "key", request, AsyncMock(), settings)
    assert result["status"] == "accepted"
    queue.enqueue_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_ingress_api_maps_rejections_and_conflicts(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = SimpleNamespace(
        body=AsyncMock(return_value=b"{}"),
        headers={},
        method="POST",
        url="https://test",
        app=SimpleNamespace(state=SimpleNamespace(job_queue=AsyncMock())),
    )
    with pytest.raises(HTTPException) as unknown:
        await api.receive("other", "key", request, AsyncMock(), settings)
    assert unknown.value.status_code == 404
    monkeypatch.setattr(api, "accept", AsyncMock(side_effect=WebhookRejected("bad", 415)))
    with pytest.raises(HTTPException) as rejected:
        await api.receive("shopify", "key", request, AsyncMock(), settings)
    assert rejected.value.status_code == 415
    monkeypatch.setattr(
        api, "accept", AsyncMock(return_value=(SimpleNamespace(id=uuid4()), "conflict"))
    )
    with pytest.raises(HTTPException) as conflict:
        await api.receive("shopify", "key", request, AsyncMock(), settings)
    assert conflict.value.status_code == 409


def test_staff_guard() -> None:
    principal = Principal("staff", uuid4(), uuid4(), Role.SUPPORT, uuid4())
    api._staff(principal)
    with pytest.raises(AuthorizationError):
        api._staff(Principal("customer", uuid4(), uuid4(), None, uuid4()))


def test_real_provider_binding_names_are_explicit(settings: Settings) -> None:
    integration = settings.model_copy(
        update={"commerce_provider": "shopify", "crm_provider": "hubspot"}
    )
    assert provider_name(integration, "order") == "shopify"
    assert provider_name(integration, "ticket") == "hubspot"
    with pytest.raises(ValueError, match="resource type"):
        provider_name(integration, "contact")


def test_reconciliation_helpers_minimize_and_sanitize() -> None:
    assert _resource_identity([], "orders/updated") == ("unknown", "")
    assert _resource_identity([{"objectId": 42}], "ticket.reply") == ("ticket", "42")
    assert _resource_identity({"admin_graphql_api_id": "gid://order/1"}, "orders/updated") == (
        "order",
        "gid://order/1",
    )
    assert _order_data(
        SimpleNamespace(
            status="open",
            fulfillment_status="fulfilled",
            tracking=None,
            refund_requests=[SimpleNamespace(status="pending")],
        )
    ) == {
        "status": "open",
        "fulfillment_status": "fulfilled",
        "tracking_status": None,
        "refund_statuses": ["pending"],
    }
    assert _order_data(
        SimpleNamespace(
            status="open",
            fulfillment_status="partial",
            tracking=SimpleNamespace(events=[SimpleNamespace(status="in_transit")]),
            refund_requests=[],
        )
    )["tracking_status"] == "in_transit"
    assert _ticket_data(SimpleNamespace(status="open", assigned_staff_ref="staff")) == {
        "status": "open",
        "assigned_staff_ref": "staff",
    }
    assert _clean_public_text("  hello\x00 <script>  ") == "hello &lt;script&gt;"


@pytest.mark.asyncio
async def test_processor_missing_event_is_safe(settings: Settings) -> None:
    session = SimpleNamespace(get=AsyncMock(return_value=None))
    assert await process(session, settings, uuid4()) == "missing"
    terminal = SimpleNamespace(organization_id=uuid4(), status=WebhookStatus.PROCESSED)
    session = SimpleNamespace(get=AsyncMock(return_value=terminal), execute=AsyncMock())
    assert await process(session, settings, uuid4()) == "processed"


@pytest.mark.asyncio
async def test_binding_requires_connection_and_reuses_existing(settings: Settings) -> None:
    values = {
        "organization_id": uuid4(),
        "customer_id": uuid4(),
        "conversation_id": uuid4(),
        "resource_type": "order",
        "external_ref": "order-1",
    }
    missing = SimpleNamespace(scalar=AsyncMock(return_value=None))
    with pytest.raises(RuntimeError, match="provider_connection_missing"):
        await bind_resource(missing, settings, **values)
    existing = SimpleNamespace(id=uuid4())
    connection = SimpleNamespace(id=uuid4())
    session = SimpleNamespace(scalar=AsyncMock(side_effect=[connection, existing]))
    assert await bind_resource(session, settings, **values) is existing


@pytest.mark.asyncio
async def test_staff_event_listing_is_minimized() -> None:
    organization_id = uuid4()
    principal = Principal("staff", organization_id, uuid4(), Role.ADMIN, uuid4())
    item = SimpleNamespace(
        id=uuid4(),
        provider="shopify",
        connection_id=uuid4(),
        external_event_id="evt",
        topic="orders/updated",
        payload_hash="f" * 64,
        received_at=datetime.now(timezone.utc),
        status=SimpleNamespace(value="failed"),
        attempts=2,
        safe_error="processing_failed",
    )
    session = SimpleNamespace(scalars=AsyncMock(return_value=[item]))
    result = await api.list_events(principal, session)
    assert result[0].payload_fingerprint == "f" * 64
    assert not hasattr(result[0], "encrypted_payload")
    with pytest.raises(HTTPException) as invalid:
        await api.list_events(principal, session, "not-a-status")
    assert invalid.value.status_code == 422
