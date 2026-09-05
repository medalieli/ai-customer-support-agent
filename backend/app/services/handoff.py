import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Literal, Protocol
from uuid import UUID

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.models import (
    AgentEvent,
    AgentRun,
    AgentThread,
    Conversation,
    ConversationOwner,
    ConversationStatus,
    Message,
    MessageRole,
    PendingAction,
    RecordStatus,
    SupportTicket,
    TicketStatus,
)
from app.providers.errors import ProviderError
from app.providers.models import ProviderContext, SupportTicketUpsert
from app.providers.ports import CrmProviderV1
from app.repositories.conversations import ConversationRepository
from app.services.audit import AuditService

ACTIVE_TICKET = {TicketStatus.OPEN, TicketStatus.IN_PROGRESS}
STAFF_STATES = {"handoff_pending", "staff_active"}


class SummaryDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issue_category: str = Field(min_length=2, max_length=80)
    customer_summary: str = Field(min_length=1, max_length=1200)
    relevant_order_refs: list[str] = Field(default_factory=list, max_length=10)


class SummaryModel(Protocol):
    async def summarize(
        self, visible_messages: list[str], allowed_order_refs: list[str], reason_code: str
    ) -> SummaryDraft: ...


class OpenAIHandoffSummaryModel:
    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key:
            raise RuntimeError("OpenAI API key is required for handoff summaries")
        self.client = AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            timeout=settings.agent_model_timeout_seconds,
            max_retries=0,
        )
        self.model = settings.agent_model

    async def summarize(
        self, visible_messages: list[str], allowed_order_refs: list[str], reason_code: str
    ) -> SummaryDraft:
        response = await self.client.responses.parse(
            model=self.model,
            instructions=(
                "Create a factual customer-visible support handoff summary from only the "
                "provided visible messages. Set issue_category to reason_code exactly. Copy the "
                "single most relevant customer message verbatim as customer_summary. Do not "
                "infer facts, sentiment, identity, "
                "payment data, addresses, secrets, or hidden reasoning. Order references "
                "must come from the allowed list."
            ),
            input=json.dumps(
                {
                    "messages": visible_messages[-20:],
                    "allowed_order_refs": allowed_order_refs,
                    "reason_code": reason_code,
                }
            ),
            text_format=SummaryDraft,
        )
        if response.output_parsed is None:
            raise ValueError("handoff summary missing")
        return response.output_parsed


def explicit_human_request(message: str) -> bool:
    return bool(
        re.search(
            r"\b(human|real person|agent|representative|support person|conseiller|humain|"
            r"personne r[ée]elle|service client)\b",
            message.casefold(),
        )
    )


def priority_for(reason: str) -> Literal["low", "normal", "high", "urgent"]:
    value = reason.casefold()
    if any(term in value for term in ("security", "account_takeover", "payment", "fraud")):
        return "urgent"
    if any(term in value for term in ("manual", "refund", "provider", "action", "maximum")):
        return "high"
    if any(term in value for term in ("ambiguous", "low_confidence", "triage_failed")):
        return "low"
    return "normal"


class HandoffError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _safe_text(value: str, limit: int) -> str:
    cleaned = " ".join(value.split())
    return cleaned[:limit]


class HandoffService:
    def __init__(self, session: AsyncSession, settings: Settings, crm: CrmProviderV1) -> None:
        self.session, self.settings, self.crm = session, settings, crm
        self.audit = AuditService(session)

    def _context(
        self, organization_id: UUID, actor_id: UUID, correlation_id: str, key: str | None = None
    ) -> ProviderContext:
        return ProviderContext(
            organization_id=organization_id,
            actor_ref=str(actor_id),
            correlation_id=correlation_id,
            idempotency_key=key,
        )

    async def _audit(
        self,
        organization_id: UUID,
        actor_type: str,
        actor_id: UUID,
        action: str,
        outcome: str,
        ticket_id: UUID | None = None,
        reason: str | None = None,
    ) -> None:
        await self.audit.record(
            organization_id,
            actor_type,
            action,
            outcome,
            actor_id=actor_id,
            target_type="support_ticket",
            target_id=ticket_id,
            reason_code=reason,
        )

    async def _event(
        self,
        organization_id: UUID,
        conversation_id: UUID,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        run = await self.session.scalar(
            select(AgentRun)
            .join(AgentThread, AgentThread.id == AgentRun.thread_id)
            .where(
                AgentRun.organization_id == organization_id,
                AgentThread.conversation_id == conversation_id,
            )
            .order_by(AgentRun.created_at.desc())
            .limit(1)
        )
        if run is None:
            return
        sequence = await self.session.scalar(
            select(func.coalesce(func.max(AgentEvent.sequence_number), 0)).where(
                AgentEvent.run_id == run.id
            )
        )
        self.session.add(
            AgentEvent(
                organization_id=organization_id,
                run_id=run.id,
                sequence_number=int(sequence or 0) + 1,
                event_type=event_type,
                payload=payload,
            )
        )

    async def _evidence(
        self, organization_id: UUID, conversation_id: UUID
    ) -> tuple[list[str], list[dict[str, object]], list[str], list[dict[str, str]]]:
        messages = list(
            await self.session.scalars(
                select(Message)
                .where(
                    Message.organization_id == organization_id,
                    Message.conversation_id == conversation_id,
                    Message.visible_to_customer.is_(True),
                )
                .order_by(Message.sequence_number)
            )
        )
        visible = [_safe_text(item.content, 1000) for item in messages]
        runs = list(
            await self.session.scalars(
                select(AgentRun)
                .join(AgentThread, AgentThread.id == AgentRun.thread_id)
                .where(
                    AgentRun.organization_id == organization_id,
                    AgentThread.conversation_id == conversation_id,
                )
                .order_by(AgentRun.created_at.desc())
                .limit(10)
            )
        )
        tools: list[dict[str, object]] = []
        citations: list[str] = []
        order_refs: list[str] = []
        for run in runs:
            state = run.state_json or {}
            for result in state.get("sanitized_results", []):
                if isinstance(result, dict):
                    tools.append(
                        {
                            "tool": str(result.get("tool", "unknown"))[:120],
                            "status": str(result.get("status", "unknown"))[:30],
                            "error_code": str(result.get("error_code") or "")[:80],
                        }
                    )
                    data = result.get("data")
                    if isinstance(data, dict) and data.get("order_number"):
                        order_refs.append(str(data["order_number"])[:40])
            for citation in state.get("citations", []):
                if isinstance(citation, dict) and citation.get("receipt_id"):
                    citations.append(str(citation["receipt_id"]))
        pending = list(
            await self.session.scalars(
                select(PendingAction).where(
                    PendingAction.organization_id == organization_id,
                    PendingAction.conversation_id == conversation_id,
                )
            )
        )
        actions = [
            {"type": item.action_type, "status": item.status.value, "action_id": str(item.id)}
            for item in pending
        ]
        return visible, tools[-20:], list(dict.fromkeys(citations))[:20], actions

    async def escalate(
        self,
        *,
        organization_id: UUID,
        customer_id: UUID,
        conversation_id: UUID,
        reason_code: str,
        correlation_id: str,
        summary_model: SummaryModel | None,
    ) -> SupportTicket:
        conversation = await self.session.scalar(
            select(Conversation)
            .where(
                Conversation.organization_id == organization_id,
                Conversation.id == conversation_id,
                Conversation.customer_id == customer_id,
            )
            .with_for_update()
        )
        if conversation is None:
            raise HandoffError("conversation_not_found")
        existing = await self.session.scalar(
            select(SupportTicket)
            .where(
                SupportTicket.organization_id == organization_id,
                SupportTicket.conversation_id == conversation_id,
                SupportTicket.status.in_(ACTIVE_TICKET),
            )
            .with_for_update()
        )
        if existing is not None:
            return existing
        visible, tools, citations, actions = await self._evidence(organization_id, conversation_id)
        order_refs = list(dict.fromkeys(re.findall(r"\b[A-Z]{2}-\d{4,}\b", " ".join(visible))))[:10]
        fallback = False
        try:
            if summary_model is None:
                raise ValueError("summary model unavailable")
            draft = await summary_model.summarize(visible, order_refs, reason_code)
            if draft.issue_category != reason_code[:80]:
                raise ValueError("unsupported issue category")
            if any(ref not in order_refs for ref in draft.relevant_order_refs):
                raise ValueError("invented order reference")
            combined = " ".join(visible).casefold()
            if draft.customer_summary.casefold() not in combined:
                raise ValueError("summary not grounded")
        except Exception:
            fallback = True
            draft = SummaryDraft(
                issue_category=reason_code[:80],
                customer_summary=(visible[-1] if visible else "Customer requested human support."),
                relevant_order_refs=order_refs,
            )
        summary: dict[str, object] = {
            "schema_version": "handoff-summary-v1",
            "source": "deterministic_fallback" if fallback else "openai_validated",
            "customer_summary": draft.customer_summary,
            "tools_attempted": tools,
            "citation_receipt_ids": citations,
            "pending_or_failed_actions": actions,
            "relevant_order_refs": draft.relevant_order_refs,
            "escalation_reason": reason_code,
            "escalated_at": datetime.now(timezone.utc).isoformat(),
        }
        ticket = SupportTicket(
            organization_id=organization_id,
            conversation_id=conversation_id,
            reason_code=reason_code[:80],
            priority=priority_for(reason_code),
            status=TicketStatus.OPEN,
            summary=summary,
            version=1,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(ticket)
                await self.session.flush()
        except IntegrityError as exc:
            found = await self.session.scalar(
                select(SupportTicket).where(
                    SupportTicket.organization_id == organization_id,
                    SupportTicket.conversation_id == conversation_id,
                    SupportTicket.status.in_(ACTIVE_TICKET),
                )
            )
            if found is None:
                raise HandoffError("ticket_conflict") from exc
            return found
        context = self._context(
            organization_id,
            customer_id,
            correlation_id,
            "handoff-"
            + hashlib.sha256(f"{organization_id}:{conversation_id}".encode()).hexdigest()[:48],
        )
        if self.settings.crm_provider != "mock":
            raise HandoffError("unsupported_provider")
        try:
            external = await self.crm.upsert_ticket(
                context,
                SupportTicketUpsert(
                    conversation_ref=str(conversation_id),
                    category=draft.issue_category,
                    priority=ticket.priority,
                    summary=draft.customer_summary,
                    status="open",
                ),
            )
        except ProviderError as exc:
            reconciled = await self.crm.find_active_ticket(context, str(conversation_id))
            if reconciled is None:
                ticket.status = TicketStatus.RESOLVED
                await self._audit(
                    organization_id,
                    "customer",
                    customer_id,
                    "handoff.failure",
                    "failed",
                    ticket.id,
                    "PROVIDER_FAILURE",
                )
                raise HandoffError("provider_failure") from exc
            external = reconciled
        ticket.provider_ref = external.external_ref
        from app.services.provider_bindings import bind_resource

        await bind_resource(
            self.session,
            self.settings,
            organization_id=organization_id,
            customer_id=customer_id,
            conversation_id=conversation_id,
            resource_type="ticket",
            external_ref=external.external_ref,
        )
        conversation.owner = ConversationOwner.AI
        conversation.ownership_state = "handoff_pending"
        await self._audit(
            organization_id,
            "customer",
            customer_id,
            "handoff.requested",
            "success",
            ticket.id,
            reason_code,
        )
        await self._audit(
            organization_id, "system", customer_id, "ticket.created", "success", ticket.id
        )
        return ticket

    async def transition(
        self,
        *,
        organization_id: UUID,
        staff_id: UUID,
        ticket_id: UUID,
        expected_version: int,
        action: str,
        correlation_id: str,
        content: str | None = None,
    ) -> SupportTicket:
        ticket = await self.session.scalar(
            select(SupportTicket)
            .where(SupportTicket.organization_id == organization_id, SupportTicket.id == ticket_id)
            .with_for_update()
        )
        if ticket is None:
            raise HandoffError("ticket_not_found")
        transition_hash = hashlib.sha256(
            f"{staff_id}:{correlation_id}:{action}:{content or ''}".encode()
        ).hexdigest()
        prior_keys = ticket.summary.get("transition_key_hashes", [])
        if isinstance(prior_keys, list) and transition_hash in prior_keys:
            return ticket
        if ticket.version != expected_version:
            raise HandoffError("stale_ticket")
        conversation = await self.session.scalar(
            select(Conversation)
            .where(
                Conversation.organization_id == organization_id,
                Conversation.id == ticket.conversation_id,
            )
            .with_for_update()
        )
        if conversation is None:
            raise HandoffError("conversation_not_found")
        if action == "claim":
            if ticket.status != TicketStatus.OPEN or ticket.assigned_staff_id is not None:
                raise HandoffError("ticket_already_claimed")
            ticket.assigned_staff_id, ticket.status = staff_id, TicketStatus.IN_PROGRESS
            conversation.owner, conversation.assigned_staff_id = ConversationOwner.STAFF, staff_id
            conversation.ownership_state = "staff_active"
            event = "staff.joined"
        elif action in {"reply", "note"}:
            if ticket.status != TicketStatus.IN_PROGRESS or ticket.assigned_staff_id != staff_id:
                raise HandoffError("staff_not_owner")
            if not content or not _safe_text(content, 4000):
                raise HandoffError("invalid_content")
            visible = action == "reply"
            await ConversationRepository(self.session).add_message(
                conversation,
                MessageRole.STAFF,
                _safe_text(content, 4000),
                conversation.locale,
                sender_staff_id=staff_id,
                visible_to_customer=visible,
            )
            event = "staff.replied" if visible else "staff.noted"
        elif action in {"resolve", "close", "return_to_ai"}:
            allowed_statuses = {TicketStatus.IN_PROGRESS}
            if action == "return_to_ai":
                allowed_statuses.add(TicketStatus.RESOLVED)
            if ticket.status not in allowed_statuses or ticket.assigned_staff_id != staff_id:
                raise HandoffError("staff_not_owner")
            if action == "return_to_ai":
                ticket.status = TicketStatus.RESOLVED
                conversation.owner, conversation.assigned_staff_id = ConversationOwner.AI, None
                conversation.ownership_state = "ai_active"
                conversation.status = ConversationStatus.OPEN
                for pending in await self.session.scalars(
                    select(PendingAction).where(
                        PendingAction.organization_id == organization_id,
                        PendingAction.conversation_id == conversation.id,
                        PendingAction.status == RecordStatus.PENDING,
                    )
                ):
                    pending.status, pending.failure_code = RecordStatus.CANCELLED, "STAFF_TAKEOVER"
                event = "ai.resumed"
            else:
                ticket.status = TicketStatus.RESOLVED
                conversation.status = ConversationStatus.RESOLVED
                conversation.ownership_state = "resolved"
                event = "ticket.resolved" if action == "resolve" else "ticket.closed"
            ticket.resolved_at = datetime.now(timezone.utc)
        else:
            raise HandoffError("invalid_transition")
        ticket.version += 1
        if ticket.provider_ref:
            context = self._context(
                organization_id,
                staff_id,
                correlation_id,
                f"ticket-{ticket.id}-{ticket.version}-{action}",
            )
            try:
                if action in {"reply", "note"}:
                    await self.crm.add_ticket_message(
                        context,
                        ticket.provider_ref,
                        _safe_text(content or "", 4000),
                        "customer" if action == "reply" else "internal",
                    )
                else:
                    current = await self.crm.get_ticket(context, ticket.provider_ref)
                    await self.crm.upsert_ticket(
                        context,
                        SupportTicketUpsert(
                            conversation_ref=str(ticket.conversation_id),
                            category=ticket.reason_code,
                            priority=ticket.priority,
                            summary=str(ticket.summary.get("customer_summary", "")),
                            status="in_progress"
                            if action == "claim"
                            else "returned_to_ai"
                            if action == "return_to_ai"
                            else "resolved",
                            assigned_staff_ref=str(staff_id),
                            version=current.version,
                        ),
                    )
            except ProviderError as exc:
                raise HandoffError("provider_" + exc.code.value) from exc
        await self._audit(organization_id, "staff", staff_id, event, "success", ticket.id)
        public_event = {
            "staff.joined": "staff_joined",
            "staff.replied": "staff_replied",
            "ticket.resolved": "resolved",
            "ticket.closed": "resolved",
            "ai.resumed": "ai_resumed",
        }.get(event)
        if public_event:
            await self._event(
                organization_id,
                ticket.conversation_id,
                public_event,
                {"ticket_id": str(ticket.id), "status": conversation.ownership_state},
            )
        ticket.summary = {
            **ticket.summary,
            "transition_key_hashes": [
                *([str(item) for item in prior_keys] if isinstance(prior_keys, list) else []),
                transition_hash,
            ][-50:],
        }
        return ticket
