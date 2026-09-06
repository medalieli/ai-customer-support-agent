import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.answers import GroundedAnswer
from app.agent.repository import AgentRepository, RunConflict
from app.agent.state import IntentLabel, IntentScore
from app.agent.triage import TriageOutput
from app.api.dependencies import get_db_session
from app.core.config import Settings
from app.domain.models import AgentRun, AgentThread, AuditEvent
from app.infrastructure.database import create_database_engine, set_tenant_scope
from app.main import create_app
from app.seed import ORGANIZATIONS, seed
from app.services.handoff import SummaryDraft

pytestmark = pytest.mark.skipif(
    os.getenv("NOVACART_RUN_DB_TESTS") != "1", reason="integration database not requested"
)


class FakeTriage:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def classify(self, message: str) -> TriageOutput:
        return TriageOutput(intents=[IntentScore(label=IntentLabel.HUMAN_HELP, confidence=0.99)])


class FakeAnswer:
    def __init__(self, settings: Settings) -> None:
        del settings

    async def answer(
        self, question: str, language: str, evidence: list[dict[str, str]]
    ) -> GroundedAnswer:
        return GroundedAnswer(supported=True, answer="safe", citation_receipt_ids=["unused"])


class FakeHandoffSummary:
    def __init__(self, settings: Settings) -> None:
        del settings

    async def summarize(
        self, visible_messages: list[str], allowed_order_refs: list[str], reason_code: str
    ) -> SummaryDraft:
        return SummaryDraft(
            issue_category=reason_code,
            customer_summary=visible_messages[-1],
            relevant_order_refs=allowed_order_refs,
        )


@pytest.fixture
async def agent_client() -> AsyncIterator[tuple[AsyncClient, async_sessionmaker[AsyncSession]]]:
    settings = Settings(
        app_env="test",
        demo_auth_enabled=True,
        demo_staff_password=SecretStr("synthetic-demo-password"),
        embedding_provider="fake",
        reranker_provider="deterministic",
        mock_commerce_url=os.getenv("NOVACART_MOCK_COMMERCE_URL", "http://localhost:8080"),
        mock_crm_url=os.getenv("NOVACART_MOCK_CRM_URL", "http://localhost:8090"),
    )
    engine = create_database_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await seed(session, "synthetic-demo-password")
    app = create_app(settings)
    app.state.agent_checkpointer = InMemorySaver()
    app.state.agent_triage_factory = FakeTriage
    app.state.agent_answer_factory = FakeAnswer
    app.state.agent_handoff_summary_factory = FakeHandoffSummary

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
async def test_staff_analytics_rbac_and_tenant_safe_shape(
    agent_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = agent_client
    await login_and_conversation(client, "amira-en", "novacart")
    assert (await client.get("/api/v1/staff/analytics?range=24h")).status_code == 403
    await client.post("/api/v1/auth/logout")
    login = await client.post(
        "/api/v1/auth/staff-login",
        json={
            "organization_slug": "novacart",
            "email": "support@novacart.test",
            "password": "synthetic-demo-password",
        },
    )
    assert login.status_code == 200
    response = await client.get("/api/v1/staff/analytics?range=24h")
    assert response.status_code == 200
    assert response.json()["conversation_volume"] >= 1
    assert not ({"messages", "customers", "conversation_ids"} & response.json().keys())
    assert (await client.get("/api/v1/staff/analytics?range=forever")).status_code == 422


@pytest.mark.asyncio
async def test_conversation_audit_rbac_and_untrusted_metadata(
    agent_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = agent_client
    conversation_id = await login_and_conversation(client, "amira-en", "novacart")
    path = f"/api/v1/staff/conversations/{conversation_id}/audit"
    assert (await client.get(path)).status_code == 403
    async with factory() as session:
        await set_tenant_scope(session, ORGANIZATIONS[0].id)
        session.add(
            AuditEvent(
                organization_id=ORGANIZATIONS[0].id,
                actor_type="agent",
                action="agent.triage_completed",
                outcome="recorded",
                target_type="conversation",
                target_id=conversation_id,
                metadata_json={
                    "status": {"secret": "PRIVATE-ADDRESS-PAYLOAD"},
                    "chain_of_thought": "PRIVATE-REASONING",
                },
            )
        )
        await session.commit()
    await client.post("/api/v1/auth/logout")
    for email in ("support@novacart.test", "admin@novacart.test"):
        login = await client.post(
            "/api/v1/auth/staff-login",
            json={
                "organization_slug": "novacart",
                "email": email,
                "password": "synthetic-demo-password",
            },
        )
        assert login.status_code == 200
        response = await client.get(path)
        assert response.status_code == 200
        assert "PRIVATE" not in response.text
        assert all(item["metadata"] == {} for item in response.json()["items"])
        assert (await client.get(f"/api/v1/staff/conversations/{uuid4()}/audit")).status_code == 404
        await client.post("/api/v1/auth/logout")
    await client.post(
        "/api/v1/auth/staff-login",
        json={
            "organization_slug": "orbit-outlet",
            "email": "support@orbit.test",
            "password": "synthetic-demo-password",
        },
    )
    assert (await client.get(path)).status_code == 404


@pytest.mark.asyncio
async def test_demo_reset_preserves_other_tenant_and_audit(
    agent_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import func

    from app import demo_reset
    from app.domain.models import Conversation

    client, factory = agent_client
    await login_and_conversation(client, "amira-en", "novacart")
    await client.post("/api/v1/auth/logout")
    other_id = await login_and_conversation(client, "nora-en", "orbit-outlet")
    settings = Settings(
        app_env="development",
        demo_auth_enabled=True,
        demo_staff_password=SecretStr("synthetic-demo-password"),
    )
    monkeypatch.setattr(demo_reset, "get_settings", lambda: settings)
    monkeypatch.setenv("NOVACART_CONFIRM_DEMO_RESET", "RESET_SYNTHETIC_NOVACART")
    async with factory() as session:
        await set_tenant_scope(session, ORGANIZATIONS[0].id)
        before = await session.scalar(select(func.count(AuditEvent.id)))
    await demo_reset.main()
    async with factory() as session:
        await set_tenant_scope(session, ORGANIZATIONS[0].id)
        assert (
            await session.scalar(
                select(func.count(Conversation.id)).where(
                    Conversation.organization_id == ORGANIZATIONS[0].id
                )
            )
            == 0
        )
        assert await session.scalar(select(func.count(AuditEvent.id))) == before
        await set_tenant_scope(session, ORGANIZATIONS[1].id)
        assert await session.get(Conversation, other_id) is not None


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
    assert first.json()["status"] == "handoff_pending"
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
    assert "event: escalation_required" in stream.text
    assert "event: ticket_created" in stream.text
    assert "prompt" not in stream.text.lower()
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
    assert resume.status_code == 409
    assert resume.json()["detail"] == "thread_not_interrupted"

    await login_and_conversation(client, "nora-en", "orbit-outlet")
    denied = await client.get(f"/api/v1/agent/runs/{run_id}/events")
    assert denied.status_code == 404


@pytest.mark.asyncio
async def test_staff_ticket_api_rbac_claim_reply_note_resolve(
    agent_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = agent_client
    conversation_id = await login_and_conversation(client, "amira-en", "novacart")
    created = await client.post(
        f"/api/v1/agent/threads/{conversation_id}/messages",
        json={"content": "I need a human representative"},
        headers={"Idempotency-Key": f"handoff-{uuid4()}"},
    )
    assert created.json()["status"] == "handoff_pending"
    assert (await client.get("/api/v1/staff/tickets")).status_code == 403
    await client.post("/api/v1/auth/logout")
    login = await client.post(
        "/api/v1/auth/staff-login",
        json={
            "organization_slug": "novacart",
            "email": "support@novacart.test",
            "password": "synthetic-demo-password",
        },
    )
    assert login.status_code == 200
    queue = await client.get("/api/v1/staff/tickets", params={"status": "open"})
    assert queue.status_code == 200
    ticket = next(item for item in queue.json() if item["conversation_id"] == str(conversation_id))
    ticket_id = ticket["id"]
    get_ticket = await client.get(f"/api/v1/staff/tickets/{ticket_id}")
    assert get_ticket.status_code == 200
    assert (await client.get(f"/api/v1/staff/tickets/{uuid4()}")).status_code == 404
    invalid = await client.post(
        f"/api/v1/staff/tickets/{ticket_id}/invented",
        json={"version": ticket["version"]},
        headers={"Idempotency-Key": f"invalid-{uuid4()}"},
    )
    assert invalid.status_code == 422
    claimed = await client.post(
        f"/api/v1/staff/tickets/{ticket_id}/claim",
        json={"version": ticket["version"]},
        headers={"Idempotency-Key": f"claim-{uuid4()}"},
    )
    assert claimed.status_code == 200 and claimed.json()["status"] == "in_progress"
    reply = await client.post(
        f"/api/v1/staff/tickets/{ticket_id}/reply",
        json={"version": claimed.json()["version"], "content": "A staff member is helping."},
        headers={"Idempotency-Key": f"reply-{uuid4()}"},
    )
    note = await client.post(
        f"/api/v1/staff/tickets/{ticket_id}/note",
        json={"version": reply.json()["version"], "content": "Private staff note."},
        headers={"Idempotency-Key": f"note-{uuid4()}"},
    )
    resolved = await client.post(
        f"/api/v1/staff/tickets/{ticket_id}/resolve",
        json={"version": note.json()["version"]},
        headers={"Idempotency-Key": f"resolve-{uuid4()}"},
    )
    assert resolved.status_code == 200 and resolved.json()["status"] == "resolved"
    stale = await client.post(
        f"/api/v1/staff/tickets/{ticket_id}/close",
        json={"version": 1},
        headers={"Idempotency-Key": f"stale-{uuid4()}"},
    )
    assert stale.status_code == 409
    timeline = await client.get(f"/api/v1/staff/tickets/{ticket_id}/audit", params={"limit": 2})
    assert timeline.status_code == 200
    first_page = timeline.json()
    assert first_page["items"]
    assert first_page["next_cursor"] is not None
    assert all("actor_id" not in item for item in first_page["items"])
    assert "Private staff note." not in timeline.text
    second_page = await client.get(
        f"/api/v1/staff/tickets/{ticket_id}/audit",
        params={"limit": 100, "cursor": first_page["next_cursor"]},
    )
    assert second_page.status_code == 200
    assert {item["id"] for item in first_page["items"]}.isdisjoint(
        item["id"] for item in second_page.json()["items"]
    )
    assert (await client.get(f"/api/v1/staff/tickets/{uuid4()}/audit")).status_code == 404
    assert (
        await client.get(
            f"/api/v1/staff/tickets/{ticket_id}/audit", params={"cursor": str(uuid4())}
        )
    ).status_code == 404
    assert (
        await client.get("/api/v1/staff/tickets", params={"status": "invalid"})
    ).status_code == 422

    await client.post("/api/v1/auth/logout")
    await login_and_conversation(client, "amira-en", "novacart")
    assert (await client.get(f"/api/v1/staff/tickets/{ticket_id}/audit")).status_code == 403
    messages = await client.get(f"/api/v1/conversations/{conversation_id}/messages")
    assert messages.status_code == 200
    bodies = [item["content"] for item in messages.json()]
    assert "A staff member is helping." in bodies
    assert "Private staff note." not in bodies


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
        loaded_run = await session.get(AgentRun, run_id)
        assert loaded_run
        loaded_run.status = "completed"
        await session.commit()

    async with factory() as session:
        await set_tenant_scope(session, org)
        persisted = await session.scalar(select(AgentRun).where(AgentRun.id == run_id))
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
