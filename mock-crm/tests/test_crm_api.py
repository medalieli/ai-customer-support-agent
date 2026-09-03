from collections.abc import AsyncIterator
from pathlib import Path

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
