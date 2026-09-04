import hashlib
import json
import re
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agent.address_parser import looks_like_address_change
from app.agent.answers import OpenAIAnswerModel
from app.agent.graph import AgentGraph
from app.agent.repository import AgentRepository, IdempotencyConflict, RunConflict
from app.agent.state import AgentState, VisibleMessage
from app.agent.tools import Permission, ToolContext, ToolGateway
from app.agent.triage import OpenAITriageModel
from app.api.dependencies import CurrentPrincipal, DatabaseSession, RequestSettings
from app.api.v1.conversations import authorized_conversation
from app.domain.models import AgentRun, AgentThread, Customer, MessageRole, PendingAction
from app.providers.factory import create_commerce_provider, create_crm_provider
from app.repositories.conversations import ConversationRepository
from app.services.address_actions import AddressActionError, AddressActionService, AddressProposal
from app.services.auth import AuthorizationError, ResourceNotFoundError
from app.services.refunds import RefundError, RefundProposal, RefundService
from app.services.sales_leads import (
    OpenAILeadExtractor,
    SalesLeadError,
    SalesLeadProposal,
    SalesLeadService,
    genuine_sales_intent,
)

router = APIRouter(prefix="/agent", tags=["agent"])


class AgentMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


class AgentRunResponse(BaseModel):
    run_id: UUID
    status: str
    duplicate: bool
    checkpoint_version: int
    confirmation: dict[str, object] | None = None
    result: dict[str, object] | None = None
    message: str | None = None


class ResumeRequest(BaseModel):
    checkpoint_version: int = Field(ge=0)
    value: dict[str, object] = Field(default_factory=dict)
    action_id: UUID | None = None
    confirmation_token: str | None = Field(default=None, min_length=32, max_length=300)
    decision: str | None = Field(default=None, min_length=2, max_length=40)


def _confirmation(proposal: object | None) -> dict[str, object] | None:
    if isinstance(proposal, SalesLeadProposal):
        return {
            "action_id": str(proposal.action_id),
            "action_hash": proposal.action_hash,
            "confirmation_token": proposal.confirmation_token,
            "expires_at": proposal.expires_at.isoformat(),
            "fields_to_store": proposal.preview,
            "purpose": "Store this sales inquiry in the CRM for a sales response.",
            "confirmation_required": True,
            "no_automatic_outreach": True,
        }
    if isinstance(proposal, RefundProposal):
        if proposal.action_id is None:
            return None
        return {
            "action_id": str(proposal.action_id),
            "action_hash": proposal.action_hash,
            "confirmation_token": proposal.confirmation_token,
            "expires_at": proposal.expires_at.isoformat() if proposal.expires_at else None,
            "order_number": proposal.order_number,
            "item": {"sku": proposal.sku, "quantity": proposal.quantity},
            "amount": {"amount": str(proposal.amount), "currency": proposal.currency},
            "outcome": proposal.outcome.value,
            "policy_version": proposal.policy_version,
            "ruleset_version": proposal.ruleset_version,
            "citations": list(proposal.citations),
            "consequences": [
                "Confirmation submits a refund request; it does not move money.",
                "Changed order or policy state requires a new preview.",
            ],
            "confirmation_required": True,
        }
    if not isinstance(proposal, AddressProposal):
        return None
    return {
        "action_id": str(proposal.action_id),
        "action_hash": proposal.action_hash,
        "confirmation_token": proposal.confirmation_token,
        "expires_at": proposal.expires_at.isoformat(),
        "order_number": proposal.order_number,
        "masked_current_address": proposal.masked_current_address,
        "proposed_address": proposal.proposed_address.model_dump(mode="json"),
        "consequences": proposal.consequences,
        "confirmation_required": True,
    }


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
            result=run.state_json or None,
        )
    sensitive_address_turn = looks_like_address_change(payload.content)
    sensitive_refund_turn = bool(
        re.search(
            r"\b(?:refund|rembours|return request|demande de retour)\b", payload.content, re.I
        )
    )
    sensitive_sales_turn = genuine_sales_intent(payload.content)
    stored_content = (
        "[shipping address change request redacted]"
        if sensitive_address_turn
        else "[refund request details redacted]"
        if sensitive_refund_turn
        else "[sales inquiry details redacted]"
        if sensitive_sales_turn
        else payload.content
    )
    await conversations.add_message(
        conversation,
        MessageRole.CUSTOMER,
        stored_content,
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
        customer_id=principal.subject_id,
        session_id=principal.session_id,
        conversation_id=conversation_id,
        run_id=run.id,
        request_message=payload.content,
        lead_extractor=(
            getattr(request.app.state, "agent_lead_extractor_factory", OpenAILeadExtractor)(
                settings
            )
            if sensitive_sales_turn
            else None
        ),
        verified_name=customer.display_name,
        verified_email=customer.email,
    )
    state = AgentState(
        thread_id=str(thread.id),
        run_id=str(run.id),
        messages=[VisibleMessage(role="user", content=stored_content)],
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
        confirmation=_confirmation(
            context.address_proposal or context.refund_proposal or context.lead_proposal
        ),
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
    serialized = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    if thread is not None:
        existing_resume = await session.scalar(
            select(AgentRun).where(
                AgentRun.organization_id == principal.organization_id,
                AgentRun.thread_id == thread.id,
                AgentRun.idempotency_key == idempotency_key,
            )
        )
        if existing_resume is not None:
            if existing_resume.request_hash != hashlib.sha256(serialized.encode()).hexdigest():
                from fastapi import HTTPException

                raise HTTPException(status_code=409, detail="resume_conflict")
            return AgentRunResponse(
                run_id=existing_resume.id,
                status=existing_resume.status,
                duplicate=True,
                checkpoint_version=thread.checkpoint_version,
                result=existing_resume.state_json or None,
            )
    if thread is None or thread.checkpoint_version != payload.checkpoint_version:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail="stale_checkpoint")
    if thread.status not in {"confirmation_required", "escalation_required"}:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail="thread_not_interrupted")
    repo = AgentRepository(session)
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
            result=run.state_json or None,
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

    pending = await session.scalar(
        select(PendingAction).where(
            PendingAction.organization_id == principal.organization_id,
            PendingAction.conversation_id == conversation_id,
            PendingAction.status == "PENDING",
        )
    )
    if pending is not None:
        action_id = payload.action_id
        token = payload.confirmation_token
        decision = payload.decision or str(payload.value.get("decision", ""))
        if action_id is None or token is None or not decision:
            from fastapi import HTTPException

            raise HTTPException(status_code=422, detail="explicit_confirmation_required")
        try:
            service: Any
            if pending.action_type == "refund_request":
                service = RefundService(session, settings, create_commerce_provider(settings))
            elif pending.action_type == "sales_lead":
                service = SalesLeadService(session, settings, create_crm_provider(settings))
            else:
                service = AddressActionService(
                    session, settings, create_commerce_provider(settings)
                )
            common = dict(
                organization_id=principal.organization_id,
                customer_id=principal.subject_id,
                session_id=principal.session_id,
                conversation_id=conversation_id,
                action_id=action_id,
                token=token,
                decision=decision,
                correlation_id=str(run.id),
            )
            if pending.action_type == "sales_lead":
                outcome = await service.decide(**common)
            else:
                outcome = await service.decide(
                    customer_ref=customer.provider_customer_ref, **common
                )
        except (AddressActionError, RefundError, SalesLeadError) as exc:
            from fastapi import HTTPException

            run.status = "failed"
            await repo.event(
                run, "action_failed", {"status": "action_failed", "reason_code": exc.code}
            )
            await session.commit()
            raise HTTPException(status_code=409, detail=exc.code) from exc
        run.status = outcome.status
        run.state_json = {
            "pending_action_id": str(outcome.action_id),
            "status": outcome.status,
            "reason_code": outcome.reason_code,
        }
        if pending.action_type == "sales_lead":
            run.state_json.update(
                {
                    key: value
                    for key, value in {
                        "contact_ref": outcome.contact_ref,
                        "lead_ref": outcome.lead_ref,
                        "note_ref": outcome.note_ref,
                        "contact_operation": outcome.contact_operation,
                        "lead_operation": outcome.lead_operation,
                    }.items()
                    if value is not None
                }
            )
        thread.status = outcome.status
        thread.interrupt_reason = None
        thread.checkpoint_version += 1
        await repo.event(
            run,
            outcome.status,
            {"status": outcome.status, "reason_code": outcome.reason_code},
        )
        await session.commit()
        return AgentRunResponse(
            run_id=run.id,
            status=outcome.status,
            duplicate=False,
            checkpoint_version=thread.checkpoint_version,
            result=run.state_json,
            message=(
                "La demande commerciale a été enregistrée dans le CRM. "
                "Aucun message marketing n’a été envoyé."
                if pending.action_type == "sales_lead"
                and outcome.status == "action_completed"
                and conversation.locale == "fr"
                else "The sales inquiry was saved in the CRM. No marketing message was sent."
                if pending.action_type == "sales_lead" and outcome.status == "action_completed"
                else "La soumission CRM a été refusée; aucune donnée n’a été écrite."
                if pending.action_type == "sales_lead" and conversation.locale == "fr"
                else "CRM submission was denied; no data was written."
                if pending.action_type == "sales_lead"
                else None
            ),
        )

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
        customer_id=principal.subject_id,
        session_id=principal.session_id,
        conversation_id=conversation_id,
        run_id=run.id,
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
