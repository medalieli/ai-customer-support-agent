import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.repository import AgentRepository, RunConflict
from app.agent.state import IntentLabel, IntentScore
from app.agent.triage import TriageOutput
from app.api.dependencies import get_db_session
from app.core.config import Settings
from app.domain.models import AgentRun, AgentThread
from app.infrastructure.database import create_database_engine, set_tenant_scope
from app.main import create_app
from app.seed import ORGANIZATIONS, seed

pytestmark = pytest.mark.skipif(
    os.getenv("NOVACART_RUN_DB_TESTS") != "1", reason="integration database not requested"
)


class FakeTriage:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def classify(self, message: str) -> TriageOutput:
        return TriageOutput(intents=[IntentScore(label=IntentLabel.HUMAN_HELP, confidence=0.99)])


@pytest.fixture
async def agent_client() -> AsyncIterator[tuple[AsyncClient, async_sessionmaker[AsyncSession]]]:
    settings = Settings(
        app_env="test",
        demo_auth_enabled=True,
        demo_staff_password=SecretStr("synthetic-demo-password"),
        embedding_provider="fake",
        reranker_provider="deterministic",
        mock_commerce_url="http://localhost:8080",
        mock_crm_url="http://localhost:8090",
    )
    engine = create_database_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await seed(session, "synthetic-demo-password")
    app = create_app(settings)
    app.state.agent_checkpointer = InMemorySaver()
    app.state.agent_triage_factory = FakeTriage

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        yield client, factory
    await engine.dispose()


async def login_and_conversation(client: AsyncClient, persona: str, org: str) -> UUID:
    response = await client.post(
        "/api/v1/auth/demo-login",
        json={"organization_slug": org, "persona_key": persona},
    )
    assert response.status_code == 200
    created = await client.post("/api/v1/conversations", json={"title": "M6"})
    assert created.status_code == 201
    return UUID(created.json()["id"])


@pytest.mark.asyncio
async def test_submission_dedup_stream_reconnect_and_tenant_isolation(
    agent_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = agent_client
    conversation_id = await login_and_conversation(client, "amira-en", "novacart")
    url = f"/api/v1/agent/threads/{conversation_id}/messages"
    headers = {"Idempotency-Key": f"stable-{uuid4()}"}
    first = await client.post(url, json={"content": "Please get a human"}, headers=headers)
    assert first.status_code == 202
    assert first.json()["status"] == "escalation_required"
    stale_resume = await client.post(
        f"/api/v1/agent/threads/{conversation_id}/resume",
        json={"checkpoint_version": 999, "value": {"acknowledged": True}},
        headers={"Idempotency-Key": f"resume-{uuid4()}"},
    )
    assert stale_resume.status_code == 409
    duplicate = await client.post(url, json={"content": "Please get a human"}, headers=headers)
    assert duplicate.status_code == 202
    assert duplicate.json()["duplicate"] is True
    conflict = await client.post(url, json={"content": "different"}, headers=headers)
    assert conflict.status_code == 409

    run_id = first.json()["run_id"]
    stream = await client.get(f"/api/v1/agent/runs/{run_id}/events")
    assert stream.status_code == 200
    assert "event: triage_completed" in stream.text
    assert "event: escalation_required" not in stream.text or "prompt" not in stream.text.lower()
    last_id = stream.text.split("id: ")[-1].splitlines()[0]
    reconnect = await client.get(
        f"/api/v1/agent/runs/{run_id}/events", headers={"Last-Event-ID": last_id}
    )
    assert reconnect.text == ""

    resume = await client.post(
        f"/api/v1/agent/threads/{conversation_id}/resume",
        json={
            "checkpoint_version": first.json()["checkpoint_version"],
            "value": {"acknowledged": True},
        },
        headers={"Idempotency-Key": f"resume-{uuid4()}"},
    )
    assert resume.status_code == 200
    assert resume.json()["status"] in {"completed", "failed"}

    await login_and_conversation(client, "nora-en", "orbit-outlet")
    denied = await client.get(f"/api/v1/agent/runs/{run_id}/events")
    assert denied.status_code == 404


@pytest.mark.asyncio
async def test_repository_persistence_concurrency_and_rls(
    agent_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = agent_client
    conversation_id = await login_and_conversation(client, "amira-en", "novacart")
    org = ORGANIZATIONS[0].id
    async with factory() as session:
        await set_tenant_scope(session, org)
        repo = AgentRepository(session)
        thread = await repo.thread(org, conversation_id)
        thread_id = thread.id
        run, created = await repo.begin_run(thread, f"repo-{uuid4()}", "safe text")
        assert created
        await repo.event(run, "triage_completed", {"intents": ["human_help"]})
        await session.commit()
        loaded = await repo.events(org, run.id, 0)
        assert len(loaded) == 1
        run_id = run.id
        with pytest.raises(RunConflict):
            await repo.begin_run(thread, f"concurrent-{uuid4()}", "second run")
        await session.rollback()
        await set_tenant_scope(session, org)
        run = await session.get(AgentRun, run_id)
        assert run
        run.status = "completed"
        await session.commit()

    async with factory() as session:
        await set_tenant_scope(session, org)
        persisted = await session.scalar(select(AgentRun).where(AgentRun.id == run.id))
        assert persisted and persisted.state_json == {}
        raw = repr(persisted.state_json).lower()
        assert "chain_of_thought" not in raw and "secret" not in raw

    other = ORGANIZATIONS[1].id
    async with factory() as session:
        await set_tenant_scope(session, other)
        assert (
            await session.scalar(
                select(AgentRun).where(AgentRun.organization_id == other, AgentRun.id == run.id)
            )
            is None
        )
        assert await AgentRepository(session).events(other, run.id, 0) == []
        assert (
            await session.scalar(
                select(AgentThread).where(
                    AgentThread.organization_id == other, AgentThread.id == thread_id
                )
            )
            is None
        )
