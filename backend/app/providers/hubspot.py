from datetime import datetime, timezone
from typing import Any

from app.providers.errors import ProviderError
from app.providers.http import ProviderHttpClient
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


class HubSpotAdapter:
    def __init__(self, http: ProviderHttpClient) -> None:
        self.http = http

    async def find_contact(self, context: ProviderContext, email: str) -> Contact | None:
        response = await self.http.request(
            "POST",
            "/crm/v3/objects/contacts/search",
            retry_safe=True,
            headers={"X-Correlation-ID": context.correlation_id},
            json={
                "filterGroups": [
                    {"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}
                ],
                "properties": [
                    "email",
                    "firstname",
                    "lastname",
                    "lifecyclestage",
                    "company",
                    "hs_language",
                ],
                "limit": 1,
            },
        )
        results = response.json().get("results", [])
        return self._contact(results[0]) if results else None

    async def upsert_contact(self, context: ProviderContext, contact: ContactUpsert) -> Contact:
        if not context.idempotency_key:
            raise ProviderError(ProviderErrorCode.VALIDATION)
        response = await self.http.request(
            "POST",
            "/crm/v3/objects/contacts/batch/upsert",
            headers={"X-Correlation-ID": context.correlation_id},
            json={
                "inputs": [
                    {
                        "id": contact.email,
                        "idProperty": "email",
                        "objectWriteTraceId": context.idempotency_key,
                        "properties": {
                            "email": contact.email,
                            "firstname": contact.first_name,
                            "lastname": contact.last_name,
                            "lifecyclestage": contact.lifecycle_stage,
                            "company": contact.company or "",
                            "hs_language": contact.locale or "",
                        },
                    }
                ]
            },
        )
        results = response.json().get("results", [])
        if not results:
            raise ProviderError(ProviderErrorCode.UNAVAILABLE)
        return self._contact(results[0])

    async def create_conversation_note(
        self, context: ProviderContext, contact_ref: str, body: str
    ) -> ConversationNote:
        if not context.idempotency_key:
            raise ProviderError(ProviderErrorCode.VALIDATION)
        timestamp = datetime.now(timezone.utc)
        response = await self.http.request(
            "POST",
            "/crm/v3/objects/notes",
            headers={"X-Correlation-ID": context.correlation_id},
            json={
                "properties": {"hs_timestamp": timestamp.isoformat(), "hs_note_body": body},
                "associations": [
                    {
                        "to": {"id": contact_ref},
                        "types": [
                            {"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}
                        ],
                    }
                ],
            },
        )
        raw: dict[str, Any] = response.json()
        return ConversationNote(
            external_ref=raw["id"], contact_ref=contact_ref, body=body, created_at=timestamp
        )

    async def find_lead(self, context: ProviderContext, contact_ref: str) -> SalesLead | None:
        response = await self.http.request(
            "POST",
            "/crm/v3/objects/leads/search",
            retry_safe=True,
            headers={"X-Correlation-ID": context.correlation_id},
            json={
                "filterGroups": [
                    {
                        "filters": [
                            {
                                "propertyName": "hs_associated_contact_id",
                                "operator": "EQ",
                                "value": contact_ref,
                            }
                        ]
                    }
                ],
                "properties": [
                    "hs_associated_contact_id",
                    "novacart_interest",
                    "novacart_business_need",
                    "novacart_budget_range",
                    "novacart_timeline",
                    "novacart_preferred_contact_method",
                ],
                "limit": 1,
            },
        )
        results = response.json().get("results", [])
        return self._lead(results[0]) if results else None

    async def upsert_lead(self, context: ProviderContext, lead: SalesLeadUpsert) -> SalesLead:
        if not context.idempotency_key:
            raise ProviderError(ProviderErrorCode.VALIDATION)
        properties = {
            "hs_associated_contact_id": lead.contact_ref,
            "novacart_interest": lead.interest,
            "novacart_business_need": lead.business_need,
            "novacart_preferred_contact_method": lead.preferred_contact_method,
        }
        if lead.budget_range is not None:
            properties["novacart_budget_range"] = lead.budget_range
        if lead.timeline is not None:
            properties["novacart_timeline"] = lead.timeline
        headers = {"X-Correlation-ID": context.correlation_id}
        if lead.version:
            headers["If-Match"] = lead.version
        response = await self.http.request(
            "POST",
            "/crm/v3/objects/leads/batch/upsert",
            headers=headers,
            json={
                "inputs": [
                    {
                        "id": lead.contact_ref,
                        "idProperty": "hs_associated_contact_id",
                        "objectWriteTraceId": context.idempotency_key,
                        "properties": properties,
                    }
                ]
            },
        )
        results = response.json().get("results", [])
        if not results:
            raise ProviderError(ProviderErrorCode.UNAVAILABLE)
        return self._lead(results[0])

    @staticmethod
    def _contact(raw: dict[str, Any]) -> Contact:
        properties = raw.get("properties") or {}
        return Contact(
            external_ref=raw["id"],
            email=properties["email"],
            first_name=properties.get("firstname") or "",
            last_name=properties.get("lastname") or "",
            lifecycle_stage=properties.get("lifecyclestage") or "lead",
            locale=properties.get("hs_language") or None,
            company=properties.get("company") or None,
            provider_status="archived" if raw.get("archived") else "active",
            version=str(raw.get("updatedAt") or raw.get("createdAt") or "unknown"),
            created_at=raw["createdAt"],
            updated_at=raw.get("updatedAt") or raw["createdAt"],
        )

    @staticmethod
    def _lead(raw: dict[str, Any]) -> SalesLead:
        p = raw.get("properties") or {}
        return SalesLead(
            external_ref=raw["id"],
            contact_ref=p["hs_associated_contact_id"],
            interest=p.get("novacart_interest") or "",
            business_need=p.get("novacart_business_need") or "",
            budget_range=p.get("novacart_budget_range") or None,
            timeline=p.get("novacart_timeline") or None,
            preferred_contact_method=p.get("novacart_preferred_contact_method") or "email",
            status="archived" if raw.get("archived") else "open",
            version=str(raw.get("updatedAt") or raw.get("createdAt") or "unknown"),
            created_at=raw["createdAt"],
            updated_at=raw.get("updatedAt") or raw["createdAt"],
        )

    async def find_active_ticket(
        self, context: ProviderContext, conversation_ref: str
    ) -> SupportTicket | None:
        response = await self.http.request(
            "POST",
            "/crm/v3/objects/tickets/search",
            retry_safe=True,
            headers={"X-Correlation-ID": context.correlation_id},
            json={
                "filterGroups": [
                    {
                        "filters": [
                            {
                                "propertyName": "novacart_conversation_ref",
                                "operator": "EQ",
                                "value": conversation_ref,
                            },
                            {
                                "propertyName": "hs_pipeline_stage",
                                "operator": "IN",
                                "values": ["open", "in_progress"],
                            },
                        ]
                    }
                ],
                "properties": [
                    "novacart_conversation_ref",
                    "subject",
                    "content",
                    "hs_ticket_priority",
                    "hs_pipeline_stage",
                    "hubspot_owner_id",
                ],
                "limit": 1,
            },
        )
        values = response.json().get("results", [])
        return self._ticket(values[0]) if values else None

    async def upsert_ticket(
        self, context: ProviderContext, ticket: SupportTicketUpsert
    ) -> SupportTicket:
        if not context.idempotency_key:
            raise ProviderError(ProviderErrorCode.VALIDATION)
        headers = {"X-Correlation-ID": context.correlation_id}
        if ticket.version:
            headers["If-Match"] = ticket.version
        response = await self.http.request(
            "POST",
            "/crm/v3/objects/tickets/batch/upsert",
            headers=headers,
            json={
                "inputs": [
                    {
                        "id": ticket.conversation_ref,
                        "idProperty": "novacart_conversation_ref",
                        "objectWriteTraceId": context.idempotency_key,
                        "properties": {
                            "novacart_conversation_ref": ticket.conversation_ref,
                            "subject": ticket.category,
                            "content": ticket.summary,
                            "hs_ticket_priority": ticket.priority,
                            "hs_pipeline_stage": ticket.status,
                            "hubspot_owner_id": ticket.assigned_staff_ref or "",
                        },
                    }
                ]
            },
        )
        values = response.json().get("results", [])
        if not values:
            raise ProviderError(ProviderErrorCode.UNAVAILABLE)
        return self._ticket(values[0])

    async def list_tickets(self, context: ProviderContext) -> list[SupportTicket]:
        response = await self.http.request(
            "GET",
            "/crm/v3/objects/tickets",
            retry_safe=True,
            headers={"X-Correlation-ID": context.correlation_id},
            params={
                "properties": "novacart_conversation_ref,subject,content,hs_ticket_priority,"
                "hs_pipeline_stage,hubspot_owner_id"
            },
        )
        return [self._ticket(item) for item in response.json().get("results", [])]

    async def get_ticket(self, context: ProviderContext, ticket_ref: str) -> SupportTicket:
        response = await self.http.request(
            "GET",
            f"/crm/v3/objects/tickets/{ticket_ref}",
            retry_safe=True,
            headers={"X-Correlation-ID": context.correlation_id},
            params={
                "properties": "novacart_conversation_ref,subject,content,hs_ticket_priority,"
                "hs_pipeline_stage,hubspot_owner_id"
            },
        )
        return self._ticket(response.json())

    async def add_ticket_message(
        self, context: ProviderContext, ticket_ref: str, body: str, visibility: str
    ) -> TicketMessage:
        if not context.idempotency_key:
            raise ProviderError(ProviderErrorCode.VALIDATION)
        timestamp = datetime.now(timezone.utc)
        response = await self.http.request(
            "POST",
            "/crm/v3/objects/notes",
            headers={"X-Correlation-ID": context.correlation_id},
            json={
                "properties": {
                    "hs_timestamp": timestamp.isoformat(),
                    "hs_note_body": body,
                    "novacart_visibility": visibility,
                },
                "associations": [
                    {
                        "to": {"id": ticket_ref},
                        "types": [
                            {"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 228}
                        ],
                    }
                ],
            },
        )
        return TicketMessage(
            external_ref=response.json()["id"],
            ticket_ref=ticket_ref,
            visibility=visibility,
            body=body,
            created_at=timestamp,
        )

    @staticmethod
    def _ticket(raw: dict[str, Any]) -> SupportTicket:
        p = raw.get("properties") or {}
        return SupportTicket(
            external_ref=raw["id"],
            conversation_ref=p.get("novacart_conversation_ref") or "",
            category=p.get("subject") or "support",
            priority=(p.get("hs_ticket_priority") or "normal").lower(),
            summary=p.get("content") or "",
            status=p.get("hs_pipeline_stage") or "open",
            assigned_staff_ref=p.get("hubspot_owner_id") or None,
            version=str(raw.get("updatedAt") or raw.get("createdAt") or "unknown"),
            created_at=raw["createdAt"],
            updated_at=raw.get("updatedAt") or raw["createdAt"],
        )
