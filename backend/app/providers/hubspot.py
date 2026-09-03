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
