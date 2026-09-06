import os
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import TypedDict
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.domain.models import AuditEvent, ConsentRecord, Conversation, Customer, PendingAction
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
from app.seed import seed
from app.services.sales_leads import LeadFields, SalesLeadError, SalesLeadService

pytestmark = pytest.mark.skipif(
    os.getenv("NOVACART_RUN_DB_TESTS") != "1", reason="database integration disabled"
)


class ProposalArgs(TypedDict):
    organization_id: UUID
    customer_id: UUID
    session_id: UUID
    conversation_id: UUID
    run_id: UUID
    fields: LeadFields


class MemoryCrm:
    def __init__(self) -> None:
        self.contacts: dict[str, Contact] = {}
        self.leads: dict[str, SalesLead] = {}
        self.notes: dict[str, ConversationNote] = {}
        self.failure: ProviderErrorCode | None = None
        self.timeout_after_contact = False

    async def find_contact(self, context: ProviderContext, email: str) -> Contact | None:
        del context
        if self.failure:
            raise ProviderError(self.failure)
        return self.contacts.get(email.lower())

    async def upsert_contact(self, context: ProviderContext, value: ContactUpsert) -> Contact:
        now = datetime.now(timezone.utc)
        old = self.contacts.get(value.email.lower())
        result = Contact(
            external_ref=old.external_ref if old else f"contact-{len(self.contacts) + 1}",
            **value.model_dump(),
            provider_status="active",
            version=str(int(old.version) + 1 if old else 1),
            created_at=old.created_at if old else now,
            updated_at=now,
        )
        self.contacts[value.email.lower()] = result
        if self.timeout_after_contact:
            self.timeout_after_contact = False
            raise ProviderError(ProviderErrorCode.TIMEOUT)
        return result

    async def find_lead(self, context: ProviderContext, contact_ref: str) -> SalesLead | None:
        del context
        return self.leads.get(contact_ref)

    async def upsert_lead(self, context: ProviderContext, value: SalesLeadUpsert) -> SalesLead:
        now = datetime.now(timezone.utc)
        old = self.leads.get(value.contact_ref)
        if old and value.version != old.version:
            raise AssertionError("optimistic version required")
        result = SalesLead(
            external_ref=old.external_ref if old else f"lead-{len(self.leads) + 1}",
            **value.model_dump(exclude={"version"}),
            version=str(int(old.version) + 1 if old else 1),
            created_at=old.created_at if old else now,
            updated_at=now,
        )
        self.leads[value.contact_ref] = result
        return result

    async def create_conversation_note(
        self, context: ProviderContext, contact_ref: str, body: str
    ) -> ConversationNote:
        key = context.idempotency_key or "missing"
        if key not in self.notes:
            self.notes[key] = ConversationNote(
                external_ref=f"note-{len(self.notes) + 1}",
                contact_ref=contact_ref,
                body=body,
                created_at=datetime.now(timezone.utc),
            )
        return self.notes[key]

    async def find_active_ticket(
        self, context: ProviderContext, conversation_ref: str
    ) -> SupportTicket | None:
        raise NotImplementedError

    async def upsert_ticket(
        self, context: ProviderContext, ticket: SupportTicketUpsert
    ) -> SupportTicket:
        raise NotImplementedError

    async def list_tickets(self, context: ProviderContext) -> list[SupportTicket]:
        raise NotImplementedError

    async def get_ticket(self, context: ProviderContext, ticket_ref: str) -> SupportTicket:
        raise NotImplementedError

    async def add_ticket_message(
        self, context: ProviderContext, ticket_ref: str, body: str, visibility: str
    ) -> TicketMessage:
        raise NotImplementedError

    async def list_ticket_messages(
        self, context: ProviderContext, ticket_ref: str
    ) -> list[TicketMessage]:
        raise NotImplementedError


@pytest.fixture
async def sales_db() -> AsyncIterator[
    tuple[async_sessionmaker[AsyncSession], Settings, Customer, Conversation]
]:
    settings = Settings(
        app_env="test", embedding_provider="fake", reranker_provider="deterministic"
    )
    engine = create_database_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await seed(session, "synthetic-demo-password")
        customer = await session.scalar(select(Customer).where(Customer.demo_key == "amira-en"))
        assert customer is not None
        conversation = Conversation(
            organization_id=customer.organization_id, customer_id=customer.id, locale="en"
        )
        session.add(conversation)
        await session.commit()
        yield factory, settings, customer, conversation
    await engine.dispose()


def fields(company: str = "Acme") -> LeadFields:
    return LeadFields(
        company=company,
        interest="Enterprise API",
        business_need="Support a distributed team",
        budget_range="10k_50k",
        timeline="1_3_months",
        preferred_contact_method="email",
    )


@pytest.mark.asyncio
async def test_create_update_deny_replay_tamper_expiry_and_audit(
    sales_db: tuple[async_sessionmaker[AsyncSession], Settings, Customer, Conversation],
) -> None:
    factory, settings, customer, conversation = sales_db
    crm = MemoryCrm()
    async with factory() as session:
        service = SalesLeadService(session, settings, crm)
        kwargs = dict(
            organization_id=customer.organization_id,
            customer_id=customer.id,
            session_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            conversation_id=conversation.id,
        )
        proposal = await service.propose(
            **kwargs,
            run_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
            verified_name=customer.display_name,
            verified_email=customer.email,
            fields=fields(),
        )
        assert proposal.preview["verified_email"] == customer.email
        assert proposal.preview["company"] == "Acme"
        result = await service.decide(
            **kwargs,
            action_id=proposal.action_id,
            token=proposal.confirmation_token,
            decision="confirm",
            correlation_id="correlation-1",
        )
        assert result.status == "action_completed" and result.contact_operation == "created"
        assert len(crm.contacts) == len(crm.leads) == len(crm.notes) == 1
        await session.commit()
        with pytest.raises(SalesLeadError, match="replayed_confirmation"):
            await service.decide(
                **kwargs,
                action_id=proposal.action_id,
                token=proposal.confirmation_token,
                decision="oui",
                correlation_id="correlation-2",
            )

        updated = await service.propose(
            **kwargs,
            run_id=UUID("abababab-abab-abab-abab-abababababab"),
            verified_name=customer.display_name,
            verified_email=customer.email,
            fields=fields("Acme France"),
        )
        update_result = await service.decide(
            **kwargs,
            action_id=updated.action_id,
            token=updated.confirmation_token,
            decision="oui",
            correlation_id="correlation-fr",
        )
        assert update_result.contact_operation == update_result.lead_operation == "updated"
        assert len(crm.contacts) == len(crm.leads) == 1
        await session.commit()

        denied = await service.propose(
            **kwargs,
            run_id=UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
            verified_name=customer.display_name,
            verified_email=customer.email,
            fields=fields(),
        )
        assert (
            await service.decide(
                **kwargs,
                action_id=denied.action_id,
                token=denied.confirmation_token,
                decision="non",
                correlation_id="correlation-3",
            )
        ).status == "action_cancelled"
        assert len(crm.notes) == 2
        await session.commit()

        expired = await service.propose(
            **kwargs,
            run_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
            verified_name=customer.display_name,
            verified_email=customer.email,
            fields=fields(),
        )
        action = await session.get(PendingAction, expired.action_id)
        assert action is not None
        action.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert (
            await service.decide(
                **kwargs,
                action_id=expired.action_id,
                token=expired.confirmation_token,
                decision="yes",
                correlation_id="correlation-4",
            )
        ).reason_code == "expired"
        await session.commit()

        tampered = await service.propose(
            **kwargs,
            run_id=UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"),
            verified_name=customer.display_name,
            verified_email=customer.email,
            fields=fields(),
        )
        with pytest.raises(SalesLeadError, match="invalid_confirmation"):
            await service.decide(
                **kwargs,
                action_id=tampered.action_id,
                token=tampered.confirmation_token + "x",
                decision="yes",
                correlation_id="correlation-5",
            )
        await session.rollback()

        with pytest.raises(SalesLeadError, match="invalid_confirmation"):
            await service.decide(
                **kwargs,
                action_id=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
                token="bad",
                decision="yes",
                correlation_id="correlation-6",
            )
        unclear = await service.propose(
            **kwargs,
            run_id=UUID("12121212-1212-1212-1212-121212121212"),
            verified_name=customer.display_name,
            verified_email=customer.email,
            fields=fields(),
        )
        with pytest.raises(SalesLeadError, match="explicit_confirmation_required"):
            await service.decide(
                **kwargs,
                action_id=unclear.action_id,
                token=unclear.confirmation_token,
                decision="maybe",
                correlation_id="correlation-7",
            )
        await session.rollback()

        failing = await service.propose(
            **kwargs,
            run_id=UUID("34343434-3434-3434-3434-343434343434"),
            verified_name=customer.display_name,
            verified_email=customer.email,
            fields=fields(),
        )
        crm.failure = ProviderErrorCode.CONFLICT
        failed = await service.decide(
            **kwargs,
            action_id=failing.action_id,
            token=failing.confirmation_token,
            decision="approve",
            correlation_id="correlation-8",
        )
        assert failed.reason_code == "conflict"
        await session.commit()
        crm.failure = None
        timeout_proposal = await service.propose(
            **kwargs,
            run_id=UUID("90909090-9090-9090-9090-909090909090"),
            verified_name=customer.display_name,
            verified_email=customer.email,
            fields=fields("Acme Reconciled"),
        )
        crm.timeout_after_contact = True
        reconciled = await service.decide(
            **kwargs,
            action_id=timeout_proposal.action_id,
            token=timeout_proposal.confirmation_token,
            decision="yes",
            correlation_id="correlation-timeout",
        )
        assert reconciled.status == "action_completed"
        assert len(crm.contacts) == len(crm.leads) == 1
        await session.commit()
        consents = (
            await session.scalars(
                select(ConsentRecord).where(
                    ConsentRecord.organization_id == customer.organization_id,
                    ConsentRecord.conversation_id == conversation.id,
                )
            )
        ).all()
        events = (
            await session.scalars(
                select(AuditEvent).where(
                    AuditEvent.organization_id == customer.organization_id,
                    AuditEvent.target_id.in_(
                        select(PendingAction.id).where(
                            PendingAction.conversation_id == conversation.id
                        )
                    ),
                    AuditEvent.action.like("sales_lead.%"),
                )
            )
        ).all()
        assert len(consents) == 4
        assert {event.action for event in events} >= {
            "sales_lead.proposed",
            "sales_lead.consented",
            "sales_lead.created",
            "sales_lead.denied",
            "sales_lead.failure",
            "sales_lead.conflict",
        }


@pytest.mark.asyncio
async def test_proposal_rejects_untrusted_identity_and_real_provider(
    sales_db: tuple[async_sessionmaker[AsyncSession], Settings, Customer, Conversation],
) -> None:
    factory, settings, customer, conversation = sales_db
    async with factory() as session:
        common: ProposalArgs = dict(
            organization_id=customer.organization_id,
            customer_id=customer.id,
            session_id=UUID("56565656-5656-5656-5656-565656565656"),
            conversation_id=conversation.id,
            run_id=UUID("78787878-7878-7878-7878-787878787878"),
            fields=fields(),
        )
        service = SalesLeadService(session, settings, MemoryCrm())
        with pytest.raises(SalesLeadError, match="invalid_verified_email"):
            await service.propose(**common, verified_name="Name", verified_email="invented")
        with pytest.raises(SalesLeadError, match="invalid_verified_name"):
            await service.propose(**common, verified_name=" ", verified_email=customer.email)
        hubspot = settings.model_copy(update={"crm_provider": "hubspot"})
        with pytest.raises(SalesLeadError, match="unsupported_provider"):
            await SalesLeadService(session, hubspot, MemoryCrm()).propose(
                **common, verified_name=customer.display_name, verified_email=customer.email
            )
