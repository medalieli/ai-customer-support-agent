from typing import Protocol

from app.providers.models import (
    Address,
    Contact,
    ContactUpsert,
    ConversationNote,
    Money,
    Order,
    ProviderContext,
    RefundRequest,
    SalesLead,
    SalesLeadUpsert,
    SupportTicket,
    SupportTicketUpsert,
    TicketMessage,
    Tracking,
)


class CommerceProviderV1(Protocol):
    async def resolve_order(self, context: ProviderContext, order_number: str) -> Order: ...
    async def get_order(self, context: ProviderContext, order_ref: str) -> Order: ...
    async def get_tracking(self, context: ProviderContext, order_ref: str) -> Tracking: ...
    async def update_address(
        self, context: ProviderContext, order_ref: str, address: Address, version: str
    ) -> Order: ...
    async def create_refund_request(
        self, context: ProviderContext, order_ref: str, amount: Money, reason: str, version: str
    ) -> RefundRequest: ...


class CrmProviderV1(Protocol):
    async def find_contact(self, context: ProviderContext, email: str) -> Contact | None: ...
    async def upsert_contact(self, context: ProviderContext, contact: ContactUpsert) -> Contact: ...
    async def find_lead(self, context: ProviderContext, contact_ref: str) -> SalesLead | None: ...
    async def upsert_lead(self, context: ProviderContext, lead: SalesLeadUpsert) -> SalesLead: ...
    async def create_conversation_note(
        self, context: ProviderContext, contact_ref: str, body: str
    ) -> ConversationNote: ...
    async def find_active_ticket(
        self, context: ProviderContext, conversation_ref: str
    ) -> SupportTicket | None: ...
    async def upsert_ticket(
        self, context: ProviderContext, ticket: SupportTicketUpsert
    ) -> SupportTicket: ...
    async def list_tickets(self, context: ProviderContext) -> list[SupportTicket]: ...
    async def get_ticket(self, context: ProviderContext, ticket_ref: str) -> SupportTicket: ...
    async def add_ticket_message(
        self, context: ProviderContext, ticket_ref: str, body: str, visibility: str
    ) -> TicketMessage: ...
