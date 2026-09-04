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
)


class MockCrmAdapter:
    def __init__(self, http: ProviderHttpClient) -> None:
        self.http = http

    @staticmethod
    def _headers(context: ProviderContext, *, write: bool = False) -> dict[str, str]:
        headers = {
            "X-Organization-Id": str(context.organization_id),
            "X-Correlation-ID": context.correlation_id,
        }
        if write:
            if not context.idempotency_key:
                raise ProviderError(ProviderErrorCode.VALIDATION)
            headers["Idempotency-Key"] = context.idempotency_key
        return headers

    async def find_contact(self, context: ProviderContext, email: str) -> Contact | None:
        response = await self.http.request(
            "GET", "/v1/contacts/by-email", headers=self._headers(context), params={"email": email}
        )
        if response.status_code == 204:
            return None
        return Contact.model_validate(response.json())

    async def upsert_contact(self, context: ProviderContext, contact: ContactUpsert) -> Contact:
        response = await self.http.request(
            "PUT",
            "/v1/contacts/by-email",
            headers=self._headers(context, write=True),
            json=contact.model_dump(mode="json"),
        )
        return Contact.model_validate(response.json())

    async def find_lead(self, context: ProviderContext, contact_ref: str) -> SalesLead | None:
        response = await self.http.request(
            "GET", f"/v1/contacts/{contact_ref}/lead", headers=self._headers(context)
        )
        if response.status_code == 204:
            return None
        return SalesLead.model_validate(response.json())

    async def upsert_lead(self, context: ProviderContext, lead: SalesLeadUpsert) -> SalesLead:
        response = await self.http.request(
            "PUT",
            f"/v1/contacts/{lead.contact_ref}/lead",
            headers={
                **self._headers(context, write=True),
                **({"If-Match": lead.version} if lead.version else {}),
            },
            json=lead.model_dump(mode="json", exclude={"version"}),
        )
        return SalesLead.model_validate(response.json())

    async def create_conversation_note(
        self, context: ProviderContext, contact_ref: str, body: str
    ) -> ConversationNote:
        response = await self.http.request(
            "POST",
            f"/v1/contacts/{contact_ref}/notes",
            headers=self._headers(context, write=True),
            json={"body": body},
        )
        return ConversationNote.model_validate(response.json())
