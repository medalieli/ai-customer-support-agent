from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app
from app.store import CrmStore

ORG = "10000000-0000-0000-0000-000000000001"
OTHER = "20000000-0000-0000-0000-000000000001"
KEY = "synthetic-internal-key"


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    path = str(tmp_path / "crm.db")
    settings = Settings(
        app_env="test",
        database_path=path,
        internal_api_key=SecretStr(KEY),
        failure_simulation_enabled=True,
    )
    CrmStore(path).initialize()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(settings), raise_app_exceptions=False),
        base_url="http://test",
    ) as value:
        yield value


def headers(org: str = ORG, key: str = KEY) -> dict[str, str]:
    return {"X-Internal-API-Key": key, "X-Organization-Id": org}


@pytest.mark.asyncio
async def test_signed_webhook_emission(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = AsyncMock()

    class Outbound:
        async def __aenter__(self) -> "Outbound":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        post = sender

    monkeypatch.setattr("app.main.httpx.AsyncClient", Outbound)
    response = await client.post(
        "/v1/webhooks/emit",
        headers=headers(),
        json={
            "event_id": "ticket-event-1",
            "topic": "ticket.reply",
            "payload": {"id": "ticket-1", "status": "open"},
            "duplicates": 2,
            "invalid_signature": True,
        },
    )
    assert response.status_code == 202
    assert sender.await_count == 2
    assert sender.await_args.kwargs["headers"]["X-Mock-Signature"] == "invalid"


@pytest.mark.asyncio
async def test_health_auth_and_seed_isolation(client: AsyncClient) -> None:
    assert (await client.get("/health/ready")).status_code == 200
    assert (
        await client.get("/v1/contacts/by-email", params={"email": "amira.benali@example.test"})
    ).status_code == 401
    assert (
        await client.get(
            "/v1/contacts/by-email",
            headers=headers(),
            params={"email": "amira.benali@example.test"},
        )
    ).status_code == 200
    assert (
        await client.get(
            "/v1/contacts/by-email",
            headers=headers(OTHER),
            params={"email": "amira.benali@example.test"},
        )
    ).status_code == 204


@pytest.mark.asyncio
async def test_idempotent_upsert_note_and_conflicts(client: AsyncClient) -> None:
    payload = {
        "email": "lead@example.test",
        "first_name": "Ari",
        "last_name": "Lee",
        "lifecycle_stage": "lead",
        "locale": "en",
    }
    write = {**headers(), "Idempotency-Key": "contact-key-001"}
    first = await client.put("/v1/contacts/by-email", headers=write, json=payload)
    replay = await client.put("/v1/contacts/by-email", headers=write, json=payload)
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    changed = await client.put(
        "/v1/contacts/by-email", headers=write, json={**payload, "first_name": "Changed"}
    )
    assert changed.status_code == 409
    ref = first.json()["external_ref"]
    note_headers = {**headers(), "Idempotency-Key": "note-key-001"}
    note = await client.post(
        f"/v1/contacts/{ref}/notes",
        headers=note_headers,
        json={"body": "Synthetic conversation summary."},
    )
    assert note.status_code == 200
    assert (
        await client.post(
            f"/v1/contacts/{ref}/notes",
            headers=note_headers,
            json={"body": "Synthetic conversation summary."},
        )
    ).json() == note.json()

    lead_payload = {
        "contact_ref": ref,
        "interest": "Enterprise analytics",
        "business_need": "Equip a distributed support team.",
        "budget_range": "10k_50k",
        "timeline": "1_3_months",
        "preferred_contact_method": "email",
    }
    lead_headers = {**headers(), "Idempotency-Key": "lead-key-001"}
    lead = await client.put(f"/v1/contacts/{ref}/lead", headers=lead_headers, json=lead_payload)
    assert lead.status_code == 200
    assert (
        await client.put(f"/v1/contacts/{ref}/lead", headers=lead_headers, json=lead_payload)
    ).json() == lead.json()
    assert (await client.get(f"/v1/contacts/{ref}/lead", headers=headers())).json() == lead.json()
    conflict = await client.put(
        f"/v1/contacts/{ref}/lead",
        headers={**headers(), "Idempotency-Key": "lead-key-002"},
        json={**lead_payload, "business_need": "Changed"},
    )
    assert conflict.status_code == 409
    updated = await client.put(
        f"/v1/contacts/{ref}/lead",
        headers={
            **headers(),
            "Idempotency-Key": "lead-key-003",
            "If-Match": lead.json()["version"],
        },
        json={**lead_payload, "business_need": "Explicitly changed need."},
    )
    assert updated.status_code == 200 and updated.json()["version"] == "2"


@pytest.mark.asyncio
async def test_lead_validation_isolation_and_not_found(client: AsyncClient) -> None:
    assert (await client.get("/v1/contacts/absent/lead", headers=headers())).status_code == 204
    payload = {
        "contact_ref": "absent",
        "interest": "Demo",
        "business_need": "Evaluate",
        "preferred_contact_method": "email",
    }
    assert (
        await client.put(
            "/v1/contacts/absent/lead",
            headers={**headers(), "Idempotency-Key": "lead-missing"},
            json=payload,
        )
    ).status_code == 404
    assert (
        await client.put(
            "/v1/contacts/other/lead",
            headers={**headers(), "Idempotency-Key": "lead-binding"},
            json=payload,
        )
    ).status_code == 422


@pytest.mark.asyncio
async def test_ticket_lifecycle_idempotency_concurrency_and_isolation(client: AsyncClient) -> None:
    payload = {
        "conversation_ref": "conversation-1",
        "category": "human_request",
        "priority": "normal",
        "summary": "Customer requested a person.",
        "status": "open",
        "assigned_staff_ref": None,
    }
    write = {**headers(), "Idempotency-Key": "ticket-create-001"}
    created = await client.put("/v1/tickets/by-conversation", headers=write, json=payload)
    assert created.status_code == 200
    assert (
        await client.put("/v1/tickets/by-conversation", headers=write, json=payload)
    ).json() == created.json()
    assert len((await client.get("/v1/tickets", headers=headers())).json()) == 1
    assert (
        await client.get(
            "/v1/tickets/active", headers=headers(), params={"conversation_ref": "conversation-1"}
        )
    ).status_code == 200
    ref = created.json()["external_ref"]
    assert (await client.get(f"/v1/tickets/{ref}", headers=headers(OTHER))).status_code == 404
    stale = await client.put(
        "/v1/tickets/by-conversation",
        headers={**headers(), "Idempotency-Key": "ticket-claim-stale"},
        json={**payload, "status": "in_progress", "assigned_staff_ref": "staff-1"},
    )
    assert stale.status_code == 409
    claimed = await client.put(
        "/v1/tickets/by-conversation",
        headers={
            **headers(),
            "Idempotency-Key": "ticket-claim-001",
            "If-Match": created.json()["version"],
        },
        json={**payload, "status": "in_progress", "assigned_staff_ref": "staff-1"},
    )
    assert claimed.status_code == 200
    message_headers = {**headers(), "Idempotency-Key": "ticket-message-001"}
    message = await client.post(
        f"/v1/tickets/{ref}/messages",
        headers=message_headers,
        json={"body": "A safe reply.", "visibility": "customer"},
    )
    assert message.status_code == 200
    assert (
        await client.post(
            f"/v1/tickets/{ref}/messages",
            headers=message_headers,
            json={"body": "A safe reply.", "visibility": "customer"},
        )
    ).json() == message.json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,status",
    [
        ("rate_limit", 429),
        ("temporary", 503),
        ("not_found", 404),
        ("invalid", 422),
        ("timeout", 504),
    ],
)
async def test_failure_simulation(client: AsyncClient, failure: str, status: int) -> None:
    response = await client.get(
        "/v1/contacts/by-email",
        headers={**headers(), "X-Mock-Failure": failure},
        params={"email": "x@example.test"},
    )
    assert response.status_code == status
    assert "synthetic-internal-key" not in response.text


def test_store_persists_and_seed_is_idempotent(tmp_path: Path) -> None:
    path = str(tmp_path / "crm.db")
    first, second = CrmStore(path), CrmStore(path)
    first.initialize()
    second.initialize()
    assert second.find(ORG, "amira.benali@example.test") is not None
