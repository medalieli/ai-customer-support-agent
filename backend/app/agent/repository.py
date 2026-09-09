import hashlib
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import AgentEvent, AgentRun, AgentThread
from app.infrastructure.database import set_tenant_scope
from app.observability import (
    AGENT_OUTCOMES,
    CITATIONS,
    CONFIRMATIONS,
    INTENTS,
    LIFECYCLE,
    TOOLS,
    bounded,
    span,
)
from app.services.audit import AuditService


class RunConflict(Exception):
    pass


class IdempotencyConflict(Exception):
    pass


class AgentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def thread(self, organization_id: UUID, conversation_id: UUID) -> AgentThread:
        await set_tenant_scope(self.session, organization_id)
        value = await self.session.scalar(
            select(AgentThread).where(
                AgentThread.organization_id == organization_id,
                AgentThread.conversation_id == conversation_id,
            )
        )
        if value:
            return value
        value = AgentThread(
            organization_id=organization_id,
            conversation_id=conversation_id,
            checkpoint_thread_id=f"tenant:{organization_id}:thread:{uuid4()}",
        )
        self.session.add(value)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            await set_tenant_scope(self.session, organization_id)
            existing = await self.session.scalar(
                select(AgentThread).where(
                    AgentThread.organization_id == organization_id,
                    AgentThread.conversation_id == conversation_id,
                )
            )
            if existing is None:
                raise
            return existing
        return value

    async def begin_run(self, thread: AgentThread, key: str, content: str) -> tuple[AgentRun, bool]:
        request_hash = hashlib.sha256(content.encode()).hexdigest()
        existing = await self.session.scalar(
            select(AgentRun).where(
                AgentRun.organization_id == thread.organization_id,
                AgentRun.thread_id == thread.id,
                AgentRun.idempotency_key == key,
            )
        )
        if existing:
            if existing.request_hash != request_hash:
                raise IdempotencyConflict
            return existing, False
        run = AgentRun(
            organization_id=thread.organization_id,
            thread_id=thread.id,
            idempotency_key=key,
            request_hash=request_hash,
            status="running",
        )
        self.session.add(run)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise RunConflict from exc
        return run, True

    async def event(self, run: AgentRun, event_type: str, payload: dict[str, object]) -> None:
        conversation_id = await self.session.scalar(
            select(AgentThread.conversation_id).where(
                AgentThread.organization_id == run.organization_id,
                AgentThread.id == run.thread_id,
            )
        )
        with span(
            "agent.event",
            **{
                "agent.event.type": event_type[:60],
                "novacart.run_id": str(run.id),
                "novacart.conversation_id": str(conversation_id or ""),
            },
        ):
            await AuditService(self.session).record(
                run.organization_id,
                "agent",
                f"agent.{event_type}",
                "recorded",
                target_type="conversation",
                target_id=conversation_id,
            )
        locale = bounded(str(payload.get("locale", "other")), {"en", "fr"})
        if event_type == "triage_completed":
            raw_intents = payload.get("intents")
            for intent in raw_intents if isinstance(raw_intents, list) else []:
                INTENTS.labels(
                    bounded(
                        str(intent),
                        {
                            "knowledge_question",
                            "order_status",
                            "account_address_change",
                            "refund_request",
                            "sales_lead",
                            "human_help",
                            "small_talk",
                            "unsupported_uncertain",
                        },
                    ),
                    locale,
                ).inc()
        if event_type in {"tool_completed", "tool_failed"}:
            TOOLS.labels(
                bounded(
                    str(payload.get("tool", "other")),
                    {
                        "search_knowledge_base",
                        "get_order_status",
                        "propose_address_change",
                        "propose_refund",
                        "propose_sales_lead",
                    },
                ),
                "success" if event_type == "tool_completed" else "failure",
            ).inc()
        if event_type in {
            "response_completed",
            "clarification_required",
            "escalation_required",
            "handoff_requested",
        }:
            outcome = {
                "response_completed": "success",
                "clarification_required": "clarification",
                "escalation_required": "escalation",
                "handoff_requested": "escalation",
            }[event_type]
            AGENT_OUTCOMES.labels(outcome, locale).inc()
        if event_type in {"action_completed", "action_cancelled", "action_failed"}:
            reason = str(payload.get("reason_code", ""))
            outcome = (
                "approved"
                if event_type == "action_completed"
                else "expired"
                if reason == "expired"
                else "conflict"
                if "conflict" in reason
                else "denied"
                if event_type == "action_cancelled"
                else "failure"
            )
            CONFIRMATIONS.labels("other", outcome).inc()
        if event_type == "citation_rejected":
            CITATIONS.labels("failure", locale).inc()
        if event_type in {"ticket_created", "handoff_requested"}:
            LIFECYCLE.labels(
                "ticket" if event_type == "ticket_created" else "handoff", "created"
            ).inc()
        sequence = await self.session.scalar(
            select(func.coalesce(func.max(AgentEvent.sequence_number), 0)).where(
                AgentEvent.run_id == run.id
            )
        )
        self.session.add(
            AgentEvent(
                organization_id=run.organization_id,
                run_id=run.id,
                sequence_number=int(sequence or 0) + 1,
                event_type=event_type,
                payload=payload,
            )
        )
        await self.session.flush()

    async def events(self, organization_id: UUID, run_id: UUID, after: int) -> list[AgentEvent]:
        await set_tenant_scope(self.session, organization_id)
        result = await self.session.scalars(
            select(AgentEvent)
            .where(
                AgentEvent.organization_id == organization_id,
                AgentEvent.run_id == run_id,
                AgentEvent.sequence_number > after,
            )
            .order_by(AgentEvent.sequence_number)
        )
        return list(result)
