from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.api.dependencies import CurrentPrincipal, DatabaseSession, RequestSettings
from app.domain.models import Role, WebhookEvent, WebhookStatus
from app.observability import job_carrier, observe_queue_depth
from app.services.auth import AuthorizationError, ResourceNotFoundError
from app.services.webhooks import WebhookRejected, accept

router = APIRouter(tags=["provider-webhooks"])


@router.post("/webhooks/{provider}/{endpoint_key}", status_code=202)
async def receive(
    provider: str,
    endpoint_key: str,
    request: Request,
    session: DatabaseSession,
    settings: RequestSettings,
) -> dict[str, str]:
    if provider not in {"shopify", "hubspot", "mock_commerce", "mock_crm"}:
        raise HTTPException(404, "unknown_provider")
    raw = await request.body()
    try:
        event, outcome = await accept(
            session,
            settings,
            provider,
            endpoint_key,
            raw,
            {k.lower(): v for k, v in request.headers.items()},
            request.method,
            str(request.url),
        )
    except WebhookRejected as exc:
        raise HTTPException(exc.status_code, exc.code) from exc
    if outcome == "conflict":
        raise HTTPException(409, "event_payload_conflict")
    if outcome == "accepted":
        await request.app.state.job_queue.enqueue_job(
            "process_provider_webhook", str(event.id), job_carrier(), _job_id=f"webhook:{event.id}"
        )
        await observe_queue_depth(request.app.state.job_queue)
    return {"id": str(event.id), "status": outcome}


class EventView(BaseModel):
    id: UUID
    provider: str
    connection_id: UUID
    external_event_id: str
    topic: str
    payload_fingerprint: str
    received_at: datetime
    status: str
    attempts: int
    safe_error: str | None


def _staff(principal: CurrentPrincipal) -> None:
    if principal.kind != "staff" or principal.role not in {Role.SUPPORT, Role.ADMIN}:
        raise AuthorizationError


def _view(item: WebhookEvent) -> EventView:
    return EventView(
        id=item.id,
        provider=item.provider,
        connection_id=item.connection_id,
        external_event_id=item.external_event_id,
        topic=item.topic,
        payload_fingerprint=item.payload_hash,
        received_at=item.received_at,
        status=item.status.value,
        attempts=item.attempts,
        safe_error=item.safe_error,
    )


@router.get("/staff/webhooks", response_model=list[EventView])
async def list_events(
    principal: CurrentPrincipal, session: DatabaseSession, status: str | None = None
) -> list[EventView]:
    _staff(principal)
    query = select(WebhookEvent).where(WebhookEvent.organization_id == principal.organization_id)
    if status:
        try:
            query = query.where(WebhookEvent.status == WebhookStatus(status))
        except ValueError as exc:
            raise HTTPException(422, "invalid_status") from exc
    return [
        _view(item)
        for item in await session.scalars(
            query.order_by(WebhookEvent.received_at.desc()).limit(100)
        )
    ]


@router.post("/staff/webhooks/{event_id}/retry", response_model=EventView)
async def retry(
    event_id: UUID, request: Request, principal: CurrentPrincipal, session: DatabaseSession
) -> EventView:
    _staff(principal)
    item = await session.scalar(
        select(WebhookEvent)
        .where(
            WebhookEvent.organization_id == principal.organization_id, WebhookEvent.id == event_id
        )
        .with_for_update()
    )
    if item is None:
        raise ResourceNotFoundError
    if item.status not in {WebhookStatus.FAILED, WebhookStatus.DEAD_LETTER}:
        raise HTTPException(409, "event_not_retryable")
    item.status, item.safe_error, item.next_attempt_at = WebhookStatus.RECEIVED, None, None
    await session.commit()
    await request.app.state.job_queue.enqueue_job(
        "process_provider_webhook", str(item.id), job_carrier()
    )
    await observe_queue_depth(request.app.state.job_queue)
    return _view(item)
