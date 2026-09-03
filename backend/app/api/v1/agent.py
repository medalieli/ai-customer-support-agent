import json
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agent.answers import OpenAIAnswerModel
from app.agent.graph import AgentGraph
from app.agent.repository import AgentRepository, IdempotencyConflict, RunConflict
from app.agent.state import AgentState, VisibleMessage
from app.agent.tools import Permission, ToolContext, ToolGateway
from app.agent.triage import OpenAITriageModel
from app.api.dependencies import CurrentPrincipal, DatabaseSession, RequestSettings
from app.api.v1.conversations import authorized_conversation
from app.domain.models import AgentRun, AgentThread, Customer, MessageRole
from app.providers.factory import create_commerce_provider, create_crm_provider
from app.repositories.conversations import ConversationRepository
from app.services.auth import AuthorizationError, ResourceNotFoundError

router = APIRouter(prefix="/agent", tags=["agent"])


class AgentMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


class AgentRunResponse(BaseModel):
    run_id: UUID
    status: str
    duplicate: bool
    checkpoint_version: int


class ResumeRequest(BaseModel):
    checkpoint_version: int = Field(ge=0)
    value: dict[str, object] = Field(default_factory=dict)


async def _authorized_run(
    session: DatabaseSession, principal: CurrentPrincipal, run_id: UUID
) -> AgentRun:
    run = await session.scalar(
        select(AgentRun).where(
            AgentRun.organization_id == principal.organization_id, AgentRun.id == run_id
        )
    )
    if run is None:
        raise ResourceNotFoundError
    return run


@router.post(
    "/threads/{conversation_id}/messages", response_model=AgentRunResponse, status_code=202
)
async def submit_message(
    conversation_id: UUID,
    payload: AgentMessageRequest,
    request: Request,
    principal: CurrentPrincipal,
    session: DatabaseSession,
    settings: RequestSettings,
    idempotency_key: str = Header(min_length=8, max_length=128, alias="Idempotency-Key"),
) -> AgentRunResponse:
    if principal.kind != "customer":
        raise AuthorizationError
    conversations = ConversationRepository(session)
    conversation = await authorized_conversation(conversations, principal, conversation_id, session)
    repo = AgentRepository(session)
    thread = await repo.thread(principal.organization_id, conversation_id)
    try:
        run, created = await repo.begin_run(thread, idempotency_key, payload.content)
    except (RunConflict, IdempotencyConflict) as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail="run_conflict") from exc
    if not created:
        return AgentRunResponse(
            run_id=run.id,
            status=run.status,
            duplicate=True,
            checkpoint_version=thread.checkpoint_version,
        )
    await conversations.add_message(
        conversation,
        MessageRole.CUSTOMER,
        payload.content,
        conversation.locale,
        sender_customer_id=principal.subject_id,
    )
    customer = await session.scalar(
        select(Customer).where(
            Customer.organization_id == principal.organization_id,
            Customer.id == principal.subject_id,
        )
    )
    if customer is None:
        raise ResourceNotFoundError
    await session.commit()

    async def emit(event_type: str, data: dict[str, object]) -> None:
        await repo.event(run, event_type, data)
        await session.commit()

    context = ToolContext(
        session=session,
        settings=settings,
        organization_id=principal.organization_id,
        actor_ref=str(principal.subject_id),
        customer_ref=customer.provider_customer_ref,
        correlation_id=str(run.id),
        commerce=create_commerce_provider(settings),
        crm=create_crm_provider(settings),
        permissions=frozenset({Permission.PUBLIC_KNOWLEDGE, Permission.CUSTOMER_READ}),
        locale=conversation.locale,
    )
    state = AgentState(
        thread_id=str(thread.id),
        run_id=str(run.id),
        messages=[VisibleMessage(role="user", content=payload.content)],
    )
    triage_factory = getattr(request.app.state, "agent_triage_factory", OpenAITriageModel)
    answer_factory = getattr(request.app.state, "agent_answer_factory", OpenAIAnswerModel)
    graph = AgentGraph(
        settings,
        triage_factory(settings),
        answer_factory(settings),
        ToolGateway(),
        context,
        emit,
        cast(AsyncPostgresSaver, request.app.state.agent_checkpointer),
    )
    try:
        config: RunnableConfig = {
            "configurable": {"thread_id": thread.checkpoint_thread_id},
            "recursion_limit": settings.agent_max_steps + 8,
        }
        compiled: Any = graph.compiled
        result: dict[str, Any] = await compiled.ainvoke(
            state,
            config,
        )
        final = AgentState.model_validate(
            {key: value for key, value in result.items() if key != "__interrupt__"}
        )
        run.state_json = final.model_dump(mode="json")
        run.status = final.status
        thread.status = final.status
        thread.interrupt_reason = final.escalation_reason
        thread.checkpoint_version += 1
        if final.messages and final.messages[-1].role == "assistant":
            await conversations.add_message(
                conversation, MessageRole.ASSISTANT, final.messages[-1].content, conversation.locale
            )
    except Exception:
        run.status = "failed"
        thread.status = "failed"
        await repo.event(run, "escalation_required", {"reason": "orchestration_failure"})
    await session.commit()
    return AgentRunResponse(
        run_id=run.id,
        status=run.status,
        duplicate=False,
        checkpoint_version=thread.checkpoint_version,
    )


@router.post("/threads/{conversation_id}/resume", response_model=AgentRunResponse)
async def resume_thread(
    conversation_id: UUID,
    payload: ResumeRequest,
    request: Request,
    principal: CurrentPrincipal,
    session: DatabaseSession,
    settings: RequestSettings,
    idempotency_key: str = Header(min_length=8, max_length=128, alias="Idempotency-Key"),
) -> AgentRunResponse:
    if principal.kind != "customer":
        raise AuthorizationError
    conversations = ConversationRepository(session)
    conversation = await authorized_conversation(conversations, principal, conversation_id, session)
    thread = await session.scalar(
        select(AgentThread).where(
            AgentThread.organization_id == principal.organization_id,
            AgentThread.conversation_id == conversation_id,
        )
    )
    if thread is None or thread.checkpoint_version != payload.checkpoint_version:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail="stale_checkpoint")
    if thread.status not in {"confirmation_required", "escalation_required"}:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail="thread_not_interrupted")
    repo = AgentRepository(session)
    serialized = json.dumps(payload.value, sort_keys=True, separators=(",", ":"))
    try:
        run, created = await repo.begin_run(thread, idempotency_key, serialized)
    except (RunConflict, IdempotencyConflict) as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail="resume_conflict") from exc
    if not created:
        return AgentRunResponse(
            run_id=run.id,
            status=run.status,
            duplicate=True,
            checkpoint_version=thread.checkpoint_version,
        )
    customer = await session.scalar(
        select(Customer).where(
            Customer.organization_id == principal.organization_id,
            Customer.id == principal.subject_id,
        )
    )
    if customer is None:
        raise ResourceNotFoundError
    await session.commit()

    async def emit(event_type: str, data: dict[str, object]) -> None:
        await repo.event(run, event_type, data)
        await session.commit()

    context = ToolContext(
        session=session,
        settings=settings,
        organization_id=principal.organization_id,
        actor_ref=str(principal.subject_id),
        customer_ref=customer.provider_customer_ref,
        correlation_id=str(run.id),
        commerce=create_commerce_provider(settings),
        crm=create_crm_provider(settings),
        permissions=frozenset({Permission.PUBLIC_KNOWLEDGE, Permission.CUSTOMER_READ}),
        locale=conversation.locale,
    )
    triage_factory = getattr(request.app.state, "agent_triage_factory", OpenAITriageModel)
    answer_factory = getattr(request.app.state, "agent_answer_factory", OpenAIAnswerModel)
    graph = AgentGraph(
        settings,
        triage_factory(settings),
        answer_factory(settings),
        ToolGateway(),
        context,
        emit,
        cast(AsyncPostgresSaver, request.app.state.agent_checkpointer),
    )
    try:
        compiled: Any = graph.compiled
        result = await compiled.ainvoke(
            Command(resume=payload.value),
            {"configurable": {"thread_id": thread.checkpoint_thread_id}},
        )
        final = AgentState.model_validate(
            {key: value for key, value in result.items() if key != "__interrupt__"}
        )
        run.state_json = final.model_dump(mode="json")
        run.status = "completed"
        thread.status = "completed"
        thread.interrupt_reason = None
        thread.checkpoint_version += 1
        await repo.event(run, "response_completed", {"resumed": True})
    except Exception:
        run.status = "failed"
        await repo.event(run, "escalation_required", {"reason": "resume_failed"})
    await session.commit()
    return AgentRunResponse(
        run_id=run.id,
        status=run.status,
        duplicate=False,
        checkpoint_version=thread.checkpoint_version,
    )


@router.get("/runs/{run_id}/events")
async def stream_events(
    run_id: UUID,
    principal: CurrentPrincipal,
    session: DatabaseSession,
    after: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    await _authorized_run(session, principal, run_id)
    cursor = max(after, int(last_event_id or 0))
    events = await AgentRepository(session).events(principal.organization_id, run_id, cursor)

    async def body() -> Any:
        for event in events:
            safe = json.dumps(event.payload, separators=(",", ":"))
            yield f"id: {event.sequence_number}\nevent: {event.event_type}\ndata: {safe}\n\n"

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
