import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.domain.models import (
    Conversation,
    ConversationOwner,
    Customer,
    Message,
    MessageRole,
    ProviderConnection,
    ProviderProjection,
    TicketStatus,
)
from app.domain.models import (
    SupportTicket as DbTicket,
)
from app.infrastructure.database import create_database_engine, set_tenant_scope
from app.providers.models import (
    Address,
    LineItem,
    Money,
    Order,
    ProviderContext,
    RefundRequest,
    SupportTicket,
    TicketMessage,
)
from app.seed import ORGANIZATIONS, STAFF, seed
from app.services.provider_bindings import bind_resource
from app.services.webhooks import accept, process

pytestmark = pytest.mark.skipif(
    os.getenv("NOVACART_RUN_DB_TESTS") != "1", reason="database integration disabled"
)


def signature(raw: bytes, event_id: str, topic: str, secret: str) -> dict[str, str]:
    return {
        "content-type": "application/json",
        "x-mock-event-id": event_id,
        "x-mock-topic": topic,
        "x-mock-timestamp": str(int(datetime.now(timezone.utc).timestamp())),
        "x-mock-signature": base64.b64encode(
            hmac.new(secret.encode(), raw, hashlib.sha256).digest()
        ).decode(),
    }


def crm_signature(raw: bytes, event_id: str, topic: str, secret: str) -> dict[str, str]:
    timestamp = str(int(datetime.now(timezone.utc).timestamp()))
    digest = hmac.new(
        secret.encode(), b"POST" + b"http://test" + raw + timestamp.encode(), hashlib.sha256
    ).digest()
    return {
        "content-type": "application/json",
        "x-mock-event-id": event_id,
        "x-mock-topic": topic,
        "x-mock-timestamp": timestamp,
        "x-mock-signature": base64.b64encode(digest).decode(),
    }


def order(ref: str, version: str, status: str, fulfillment: str) -> Order:
    now = datetime.now(timezone.utc)
    money = Money(amount="10", currency="USD")
    return Order(
        external_ref=ref,
        order_number="NC-9001",
        version=version,
        status=status,
        fulfillment_status=fulfillment,
        placed_at=now,
        line_items=[
            LineItem(
                sku="S",
                name="Synthetic",
                quantity=1,
                unit_price=money,
                fulfillment_status=fulfillment,
            )
        ],
        subtotal=money,
        shipping=Money(amount="0", currency="USD"),
        tax=Money(amount="0", currency="USD"),
        total=money,
        shipping_address=Address(
            recipient="Synthetic",
            line1="redacted",
            city="Test",
            region="T",
            postal_code="00000",
            country_code="US",
        ),
        refund_requests=[
            RefundRequest(
                external_ref="refund-1",
                status="pending",
                requested_at=now,
                amount=money,
                reason="test",
            )
        ],
    )


class Commerce:
    def __init__(self, value: Order):
        self.value = value
        self.calls: list[ProviderContext] = []

    async def get_order(self, context: ProviderContext, order_ref: str) -> Order:
        self.calls.append(context)
        assert order_ref == self.value.external_ref
        return self.value


class Crm:
    def __init__(self, ticket: SupportTicket, messages: list[TicketMessage]):
        self.ticket, self.messages = ticket, messages

    async def get_ticket(self, context: ProviderContext, ticket_ref: str) -> SupportTicket:
        assert ticket_ref == self.ticket.external_ref
        return self.ticket

    async def list_ticket_messages(
        self, context: ProviderContext, ticket_ref: str
    ) -> list[TicketMessage]:
        return self.messages


@pytest.fixture
async def setup() -> tuple[
    AsyncSession, Settings, Customer, Conversation, ProviderConnection, ProviderConnection
]:
    settings = Settings(app_env="test")
    engine = create_database_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await seed(session, "synthetic-demo-password")
        await set_tenant_scope(session, ORGANIZATIONS[0].id)
        customer = await session.get(Customer, "10000000-0000-0000-0000-000000000201")
        assert customer
        conversation = Conversation(
            organization_id=customer.organization_id,
            customer_id=customer.id,
            locale="en",
            title="Webhook reconciliation",
        )
        session.add(conversation)
        await session.flush()
        commerce_connection = await session.scalar(
            select(ProviderConnection).where(
                ProviderConnection.organization_id == customer.organization_id,
                ProviderConnection.provider == "mock_commerce",
            )
        )
        crm_connection = await session.scalar(
            select(ProviderConnection).where(
                ProviderConnection.organization_id == customer.organization_id,
                ProviderConnection.provider == "mock_crm",
            )
        )
        assert commerce_connection and crm_connection
        await session.commit()
        yield session, settings, customer, conversation, commerce_connection, crm_connection
    await engine.dispose()


@pytest.mark.asyncio
async def test_authoritative_order_sync_once_ordered_and_multi_conversation(
    setup: tuple[
        AsyncSession, Settings, Customer, Conversation, ProviderConnection, ProviderConnection
    ],
) -> None:
    session, settings, customer, conversation, connection, _ = setup
    second = Conversation(
        organization_id=customer.organization_id,
        customer_id=customer.id,
        locale="en",
        title="Second binding",
    )
    session.add(second)
    await session.flush()
    for target in (conversation, second):
        await bind_resource(
            session,
            settings,
            organization_id=customer.organization_id,
            customer_id=customer.id,
            conversation_id=target.id,
            resource_type="order",
            external_ref="order-9001",
            provider_customer_ref=customer.provider_customer_ref,
        )
    await session.commit()
    authoritative = Commerce(order("order-9001", "7", "open", "fulfilled"))
    now = datetime.now(timezone.utc)
    raw = json.dumps(
        {"id": "order-9001", "occurred_at": now.isoformat(), "status": "payload-lie"}
    ).encode()
    event, _ = await accept(
        session,
        settings,
        "mock_commerce",
        connection.endpoint_key,
        raw,
        signature(
            raw,
            f"reconcile-order-1-{conversation.id}",
            "fulfillment.updated",
            settings.mock_commerce_webhook_secret.get_secret_value(),
        ),
        "POST",
        "http://test",
    )
    assert await process(session, settings, event.id, commerce=authoritative) == "processed"
    assert (
        len(authoritative.calls) == 1
        and authoritative.calls[0].customer_ref == customer.provider_customer_ref
    )
    repeated, _ = await accept(
        session,
        settings,
        "mock_commerce",
        connection.endpoint_key,
        raw,
        signature(
            raw,
            f"reconcile-order-2-{conversation.id}",
            "fulfillment.updated",
            settings.mock_commerce_webhook_secret.get_secret_value(),
        ),
        "POST",
        "http://test",
    )
    assert await process(session, settings, repeated.id, commerce=authoritative) == "stale"
    messages = list(
        await session.scalars(
            select(Message).where(
                Message.conversation_id.in_([conversation.id, second.id]),
                Message.role == MessageRole.SYSTEM_EVENT,
            )
        )
    )
    assert len(messages) == 2 and all(
        "fulfilled" in item.content and "payload-lie" not in item.content for item in messages
    )
    projection = await session.scalar(
        select(ProviderProjection).where(ProviderProjection.external_ref == "order-9001")
    )
    assert projection and projection.version == "7"
    older_raw = json.dumps(
        {"id": "order-9001", "occurred_at": (now - timedelta(days=1)).isoformat()}
    ).encode()
    older, _ = await accept(
        session,
        settings,
        "mock_commerce",
        connection.endpoint_key,
        older_raw,
        signature(
            older_raw,
            f"reconcile-order-old-{conversation.id}",
            "order.updated",
            settings.mock_commerce_webhook_secret.get_secret_value(),
        ),
        "POST",
        "http://test",
    )
    assert await process(session, settings, older.id, commerce=authoritative) == "stale"


@pytest.mark.asyncio
async def test_crm_assignment_public_reply_and_internal_note_privacy(
    setup: tuple[
        AsyncSession, Settings, Customer, Conversation, ProviderConnection, ProviderConnection
    ],
) -> None:
    session, settings, customer, conversation, _, connection = setup
    ticket_ref = f"ticket-{conversation.id}"
    local = DbTicket(
        organization_id=customer.organization_id,
        conversation_id=conversation.id,
        reason_code="support",
        priority="normal",
        status=TicketStatus.OPEN,
        summary={},
        provider_ref=ticket_ref,
        version=1,
    )
    session.add(local)
    await bind_resource(
        session,
        settings,
        organization_id=customer.organization_id,
        customer_id=customer.id,
        conversation_id=conversation.id,
        resource_type="ticket",
        external_ref=ticket_ref,
    )
    await session.commit()
    now = datetime.now(timezone.utc)
    staff_id = STAFF[0][0]
    ticket = SupportTicket(
        external_ref=ticket_ref,
        conversation_ref=str(conversation.id),
        category="support",
        priority="normal",
        summary="ignored",
        status="in_progress",
        assigned_staff_ref=str(staff_id),
        version="4",
        created_at=now,
        updated_at=now,
    )
    public = TicketMessage(
        external_ref="reply-1",
        ticket_ref=ticket_ref,
        visibility="customer",
        body="Staff update <script> is data",
        created_at=now,
    )
    internal = TicketMessage(
        external_ref="note-1",
        ticket_ref=ticket_ref,
        visibility="internal",
        body="PRIVATE INTERNAL NOTE",
        created_at=now,
    )
    crm = Crm(ticket, [internal, public])
    raw = json.dumps({"ticket_ref": ticket_ref, "occurred_at": now.isoformat()}).encode()
    event, _ = await accept(
        session,
        settings,
        "mock_crm",
        connection.endpoint_key,
        raw,
        crm_signature(
            raw,
            f"crm-reply-1-{conversation.id}",
            "ticket.reply",
            settings.mock_crm_webhook_secret.get_secret_value(),
        ),
        "POST",
        "http://test",
    )
    assert await process(session, settings, event.id, crm=crm) == "processed"
    await session.refresh(conversation)
    await session.refresh(local)
    assert conversation.owner == ConversationOwner.STAFF and local.assigned_staff_id == staff_id
    visible = list(
        await session.scalars(
            select(Message).where(
                Message.conversation_id == conversation.id, Message.visible_to_customer.is_(True)
            )
        )
    )
    assert sum("Staff update" in item.content for item in visible) == 1
    assert all("PRIVATE INTERNAL NOTE" not in item.content for item in visible)
    note_raw = json.dumps(
        {"ticket_ref": ticket_ref, "occurred_at": (now + timedelta(seconds=1)).isoformat()}
    ).encode()
    note, _ = await accept(
        session,
        settings,
        "mock_crm",
        connection.endpoint_key,
        note_raw,
        crm_signature(
            note_raw,
            f"crm-note-1-{conversation.id}",
            "ticket.note",
            settings.mock_crm_webhook_secret.get_secret_value(),
        ),
        "POST",
        "http://test",
    )
    assert await process(session, settings, note.id, crm=crm) in {"processed", "stale"}
    count = await session.scalar(
        select(func.count(Message.id)).where(Message.conversation_id == conversation.id)
    )
    assert count == len(visible)
