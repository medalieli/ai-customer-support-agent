import asyncio
import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, Query, Response

from app.config import Settings, get_settings
from app.errors import CrmError, register_handlers
from app.schemas import Contact, ContactUpsert, Lead, LeadUpsert, Note, NoteCreate
from app.store import CrmStore


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    store = CrmStore(resolved.database_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        store.initialize()
        yield

    app = FastAPI(title="NovaCart Mock CRM API", version="1.0.0", lifespan=lifespan)
    register_handlers(app)

    def scope(
        api_key: Annotated[str | None, Header(alias="X-Internal-API-Key")] = None,
        organization_id: Annotated[str | None, Header(alias="X-Organization-Id")] = None,
    ) -> str:
        if not api_key or not hmac.compare_digest(
            api_key, resolved.internal_api_key.get_secret_value()
        ):
            raise CrmError(401, "unauthenticated", "Internal authentication is required.")
        try:
            return str(UUID(organization_id or ""))
        except ValueError as exc:
            raise CrmError(422, "validation", "The organization identifier is invalid.") from exc

    async def failure(
        org: Annotated[str, Depends(scope)],
        value: Annotated[
            Literal["timeout", "rate_limit", "temporary", "not_found", "invalid"] | None,
            Header(alias="X-Mock-Failure"),
        ] = None,
    ) -> None:
        del org
        if value is None:
            return
        if not resolved.failure_simulation_enabled or resolved.app_env == "production":
            raise CrmError(403, "forbidden", "Failure simulation is disabled.")
        if value == "timeout":
            await asyncio.sleep(resolved.simulated_timeout_seconds)
            raise CrmError(504, "timeout", "The simulated request timed out.", retryable=True)
        if value == "rate_limit":
            raise CrmError(
                429,
                "rate_limited",
                "The simulated rate limit was reached.",
                retryable=True,
                retry_after=2,
            )
        if value == "temporary":
            raise CrmError(
                503, "unavailable", "The simulated service is unavailable.", retryable=True
            )
        if value == "not_found":
            raise CrmError(404, "not_found", "The contact was not found.")
        raise CrmError(422, "validation", "The simulated request is invalid.")

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"name": "NovaCart Mock CRM API", "version": "v1"}

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready")
    async def ready() -> dict[str, str]:
        store.initialize()
        return {"status": "ready", "storage": "available"}

    @app.get("/v1/contacts/by-email", response_model=Contact | None)
    async def find(
        email: Annotated[str, Query()],
        org: Annotated[str, Depends(scope)],
        checked: Annotated[None, Depends(failure)],
        response: Response,
    ) -> Contact | None:
        del checked
        result = store.find(org, email)
        if result is None:
            response.status_code = 204
            return None
        return Contact.model_validate(result)

    @app.put("/v1/contacts/by-email", response_model=Contact)
    async def upsert(
        payload: ContactUpsert,
        org: Annotated[str, Depends(scope)],
        checked: Annotated[None, Depends(failure)],
        key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> Contact:
        del checked
        if not key:
            raise CrmError(422, "validation", "Idempotency-Key is required.")
        return Contact.model_validate(store.upsert(org, key, payload.model_dump(mode="json")))

    @app.post("/v1/contacts/{contact_ref}/notes", response_model=Note)
    async def note(
        contact_ref: str,
        payload: NoteCreate,
        org: Annotated[str, Depends(scope)],
        checked: Annotated[None, Depends(failure)],
        key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> Note:
        del checked
        if not key:
            raise CrmError(422, "validation", "Idempotency-Key is required.")
        return Note.model_validate(store.add_note(org, contact_ref, key, payload.body))

    @app.get("/v1/contacts/{contact_ref}/lead", response_model=Lead | None)
    async def find_lead(
        contact_ref: str,
        org: Annotated[str, Depends(scope)],
        checked: Annotated[None, Depends(failure)],
        response: Response,
    ) -> Lead | None:
        del checked
        result = store.find_lead(org, contact_ref)
        if result is None:
            response.status_code = 204
            return None
        return Lead.model_validate(result)

    @app.put("/v1/contacts/{contact_ref}/lead", response_model=Lead)
    async def upsert_lead(
        contact_ref: str,
        payload: LeadUpsert,
        org: Annotated[str, Depends(scope)],
        checked: Annotated[None, Depends(failure)],
        key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
        version: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> Lead:
        del checked
        if not key or payload.contact_ref != contact_ref:
            raise CrmError(422, "validation", "Valid idempotency and contact binding are required.")
        return Lead.model_validate(
            store.upsert_lead(org, key, payload.model_dump(mode="json"), version)
        )

    return app
