import os
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.domain.models import (
    AgentRun,
    AgentThread,
    AuditEvent,
    Conversation,
    Customer,
    Message,
    MessageRole,
    OrganizationMembership,
)
from app.domain.models import (
    SupportTicket as DbTicket,
)
from app.infrastructure.database import create_database_engine
from app.providers.errors import ProviderError
from app.providers.models import (
    Contact,
    ContactUpsert,
    ConversationNote,
    ProviderContext,
    ProviderErrorCode,
    SalesLead,
    SalesLeadUpsert,
    SupportTicket,
    SupportTicketUpsert,
    TicketMessage,
)
from app.repositories.conversations import ConversationRepository
from app.seed import seed
from app.services.handoff import HandoffError, HandoffService, SummaryDraft

pytestmark = pytest.mark.skipif(
    os.getenv("NOVACART_RUN_DB_TESTS") != "1", reason="database integration disabled"
)


class GroundedSummary:
    async def summarize(
        self, visible_messages: list[str], allowed_order_refs: list[str], reason_code: str
    ) -> SummaryDraft:
        return SummaryDraft(
            issue_category=reason_code,
            customer_summary=visible_messages[-1],
            relevant_order_refs=allowed_order_refs,
        )


class MemoryTicketCrm:
    def __init__(self) -> None:
        self.tickets: dict[str, SupportTicket] = {}
        self.messages: dict[str, TicketMessage] = {}
        self.timeout_after_write = False
        self.fail_before_write = False

    async def find_contact(self, context: ProviderContext, email: str) -> Contact | None:
        raise NotImplementedError

    async def upsert_contact(self, context: ProviderContext, contact: ContactUpsert) -> Contact:
        raise NotImplementedError

    async def find_lead(self, context: ProviderContext, contact_ref: str) -> SalesLead | None:
        raise NotImplementedError

    async def upsert_lead(self, context: ProviderContext, lead: SalesLeadUpsert) -> SalesLead:
        raise NotImplementedError

    async def create_conversation_note(
        self, context: ProviderContext, contact_ref: str, body: str
    ) -> ConversationNote:
        raise NotImplementedError

    async def find_active_ticket(
        self, context: ProviderContext, conversation_ref: str
    ) -> SupportTicket | None:
        del context
        value = self.tickets.get(conversation_ref)
        return value if value and value.status in {"open", "in_progress"} else None

    async def upsert_ticket(
        self, context: ProviderContext, ticket: SupportTicketUpsert
    ) -> SupportTicket:
        del context
        if self.fail_before_write:
            raise ProviderError(ProviderErrorCode.UNAVAILABLE)
        old = self.tickets.get(ticket.conversation_ref)
        if old and ticket.version != old.version:
            raise AssertionError("optimistic version required")
        now = datetime.now(timezone.utc)
        value = SupportTicket(
            external_ref=old.external_ref if old else f"ticket-{len(self.tickets) + 1}",
            conversation_ref=ticket.conversation_ref,
            category=ticket.category,
            priority=ticket.priority,
            summary=ticket.summary,
            status=ticket.status,
            assigned_staff_ref=ticket.assigned_staff_ref,
            version=str(int(old.version) + 1 if old else 1),
            created_at=old.created_at if old else now,
            updated_at=now,
        )
        self.tickets[ticket.conversation_ref] = value
        if self.timeout_after_write:
            self.timeout_after_write = False
            raise ProviderError(ProviderErrorCode.TIMEOUT)
        return value

    async def list_tickets(self, context: ProviderContext) -> list[SupportTicket]:
        del context
        return list(self.tickets.values())

    async def get_ticket(self, context: ProviderContext, ticket_ref: str) -> SupportTicket:
        del context
        return next(item for item in self.tickets.values() if item.external_ref == ticket_ref)

    async def add_ticket_message(
        self, context: ProviderContext, ticket_ref: str, body: str, visibility: str
    ) -> TicketMessage:
        key = context.idempotency_key or "missing"
        if key not in self.messages:
            self.messages[key] = TicketMessage(
                external_ref=f"message-{len(self.messages) + 1}",
                ticket_ref=ticket_ref,
                visibility=visibility,
                body=body,
                created_at=datetime.now(timezone.utc),
            )
        return self.messages[key]


@pytest.fixture
async def handoff_db() -> AsyncIterator[
    tuple[async_sessionmaker[AsyncSession], Settings, Customer, Conversation, UUID]
]:
    settings = Settings(app_env="test", embedding_provider="fake", crm_provider="mock")
    engine = create_database_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await seed(session, "synthetic-demo-password")
        customer = await session.scalar(select(Customer).where(Customer.demo_key == "amira-en"))
        assert customer is not None
        staff_id = await session.scalar(
            select(OrganizationMembership.staff_user_id).where(
                OrganizationMembership.organization_id == customer.organization_id
            )
        )
        assert staff_id is not None
        conversation = Conversation(
            organization_id=customer.organization_id, customer_id=customer.id, locale="en"
        )
        session.add(conversation)
        await session.flush()
        await ConversationRepository(session).add_message(
            conversation,
            MessageRole.CUSTOMER,
            "I want a human about order NC-12345.",
            "en",
            sender_customer_id=customer.id,
        )
        thread = AgentThread(
            organization_id=customer.organization_id,
            conversation_id=conversation.id,
            checkpoint_thread_id=f"m11-{conversation.id}",
        )
        session.add(thread)
        await session.flush()
        session.add(
            AgentRun(
                organization_id=customer.organization_id,
                thread_id=thread.id,
                idempotency_key=f"m11-{conversation.id}",
                request_hash="a" * 64,
                state_json={
                    "sanitized_results": [
                        {
                            "tool": "get_order",
                            "status": "failed",
                            "error_code": "timeout",
                            "data": {"order_number": "NC-12345"},
                        },
                        "ignored",
                    ],
                    "citations": [{"receipt_id": "receipt-safe"}, "ignored"],
                },
            )
        )
        await session.commit()
        yield factory, settings, customer, conversation, staff_id
    await engine.dispose()


@pytest.mark.asyncio
async def test_ticket_dedup_lifecycle_privacy_and_restart(
    handoff_db: tuple[async_sessionmaker[AsyncSession], Settings, Customer, Conversation, UUID],
) -> None:
    factory, settings, customer, conversation, staff_id = handoff_db
    crm = MemoryTicketCrm()
    async with factory() as session:
        service = HandoffService(session, settings, crm)
        ticket = await service.escalate(
            organization_id=customer.organization_id,
            customer_id=customer.id,
            conversation_id=conversation.id,
            reason_code="explicit_human_request",
            correlation_id="request-1",
            summary_model=GroundedSummary(),
        )
        duplicate = await service.escalate(
            organization_id=customer.organization_id,
            customer_id=customer.id,
            conversation_id=conversation.id,
            reason_code="explicit_human_request",
            correlation_id="request-2",
            summary_model=GroundedSummary(),
        )
        assert duplicate.id == ticket.id
        assert ticket.summary["source"] == "openai_validated"
        assert ticket.summary["relevant_order_refs"] == ["NC-12345"]
        assert ticket.summary["tools_attempted"][0]["tool"] == "get_order"
        assert ticket.summary["citation_receipt_ids"] == ["receipt-safe"]
        claimed = await service.transition(
            organization_id=customer.organization_id,
            staff_id=staff_id,
            ticket_id=ticket.id,
            expected_version=1,
            action="claim",
            correlation_id="claim-001",
        )
        assert claimed.version == 2
        with pytest.raises(HandoffError, match="invalid_content"):
            await service.transition(
                organization_id=customer.organization_id,
                staff_id=staff_id,
                ticket_id=ticket.id,
                expected_version=2,
                action="reply",
                correlation_id="empty-reply",
            )
        with pytest.raises(HandoffError, match="invalid_transition"):
            await service.transition(
                organization_id=customer.organization_id,
                staff_id=staff_id,
                ticket_id=ticket.id,
                expected_version=2,
                action="invented",
                correlation_id="invalid-action",
            )
        with pytest.raises(HandoffError, match="stale_ticket"):
            await service.transition(
                organization_id=customer.organization_id,
                staff_id=staff_id,
                ticket_id=ticket.id,
                expected_version=1,
                action="claim",
                correlation_id="claim-002",
            )
        await service.transition(
            organization_id=customer.organization_id,
            staff_id=staff_id,
            ticket_id=ticket.id,
            expected_version=2,
            action="reply",
            content="I can help with that order.",
            correlation_id="reply-001",
        )
        noted = await service.transition(
            organization_id=customer.organization_id,
            staff_id=staff_id,
            ticket_id=ticket.id,
            expected_version=3,
            action="note",
            content="Private investigation note.",
            correlation_id="note-001",
        )
        replay = await service.transition(
            organization_id=customer.organization_id,
            staff_id=staff_id,
            ticket_id=ticket.id,
            expected_version=3,
            action="note",
            content="Private investigation note.",
            correlation_id="note-001",
        )
        assert replay.version == noted.version == 4
        resumed = await service.transition(
            organization_id=customer.organization_id,
            staff_id=staff_id,
            ticket_id=ticket.id,
            expected_version=4,
            action="return_to_ai",
            correlation_id="resume-001",
        )
        assert resumed.version == 5
        await session.commit()
        assert len(crm.tickets) == 1 and len(crm.messages) == 2

    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count(DbTicket.id)).where(
                    DbTicket.organization_id == customer.organization_id,
                    DbTicket.conversation_id == conversation.id,
                )
            )
            == 1
        )
        restored = await session.get(Conversation, conversation.id)
        assert restored is not None and restored.ownership_state == "ai_active"
        visible = list(
            await session.scalars(
                select(Message).where(
                    Message.conversation_id == conversation.id,
                    Message.visible_to_customer.is_(True),
                )
            )
        )
        assert "Private investigation note." not in [item.content for item in visible]
        audits = list(
            await session.scalars(
                select(AuditEvent).where(AuditEvent.organization_id == customer.organization_id)
            )
        )
        assert {item.action for item in audits} >= {
            "handoff.requested",
            "ticket.created",
            "staff.joined",
            "staff.replied",
            "staff.noted",
            "ai.resumed",
        }


@pytest.mark.asyncio
async def test_summary_failure_falls_back_and_cross_tenant_is_hidden(
    handoff_db: tuple[async_sessionmaker[AsyncSession], Settings, Customer, Conversation, UUID],
) -> None:
    factory, settings, customer, conversation, _ = handoff_db
    async with factory() as session:
        service = HandoffService(session, settings, MemoryTicketCrm())
        ticket = await service.escalate(
            organization_id=customer.organization_id,
            customer_id=customer.id,
            conversation_id=conversation.id,
            reason_code="low_confidence",
            correlation_id="fallback-1",
            summary_model=None,
        )
        assert ticket.summary["source"] == "deterministic_fallback"
        with pytest.raises(HandoffError, match="conversation_not_found"):
            await service.escalate(
                organization_id=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
                customer_id=customer.id,
                conversation_id=conversation.id,
                reason_code="explicit_human_request",
                correlation_id="cross-tenant",
                summary_model=None,
            )


@pytest.mark.asyncio
async def test_provider_timeout_reconciles_and_failure_is_safe(
    handoff_db: tuple[async_sessionmaker[AsyncSession], Settings, Customer, Conversation, UUID],
) -> None:
    factory, settings, customer, _, staff_id = handoff_db
    crm = MemoryTicketCrm()
    async with factory() as session:
        first = Conversation(
            organization_id=customer.organization_id, customer_id=customer.id, locale="fr"
        )
        second = Conversation(
            organization_id=customer.organization_id, customer_id=customer.id, locale="en"
        )
        session.add_all([first, second])
        await session.flush()
        crm.timeout_after_write = True
        reconciled = await HandoffService(session, settings, crm).escalate(
            organization_id=customer.organization_id,
            customer_id=customer.id,
            conversation_id=first.id,
            reason_code="provider_repeated_failure",
            correlation_id="timeout-reconcile",
            summary_model=None,
        )
        assert reconciled.provider_ref == "ticket-1"
        claimed = await HandoffService(session, settings, crm).transition(
            organization_id=customer.organization_id,
            staff_id=staff_id,
            ticket_id=reconciled.id,
            expected_version=1,
            action="claim",
            correlation_id="claim-timeout-ticket",
        )
        resolved = await HandoffService(session, settings, crm).transition(
            organization_id=customer.organization_id,
            staff_id=staff_id,
            ticket_id=reconciled.id,
            expected_version=claimed.version,
            action="resolve",
            correlation_id="resolve-timeout-ticket",
        )
        assert resolved.status.value == "resolved"
        crm.fail_before_write = True
        with pytest.raises(HandoffError, match="provider_failure"):
            await HandoffService(session, settings, crm).escalate(
                organization_id=customer.organization_id,
                customer_id=customer.id,
                conversation_id=second.id,
                reason_code="provider_repeated_failure",
                correlation_id="provider-failure",
                summary_model=None,
            )
