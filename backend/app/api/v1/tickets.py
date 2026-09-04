from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.dependencies import CurrentPrincipal, DatabaseSession, RequestSettings
from app.domain.models import Role, SupportTicket, TicketStatus
from app.providers.factory import create_crm_provider
from app.services.auth import AuthorizationError, ResourceNotFoundError
from app.services.handoff import HandoffError, HandoffService

router = APIRouter(prefix="/staff/tickets", tags=["staff-tickets"])


class TicketResponse(BaseModel):
    id: UUID
    conversation_id: UUID
    reason_code: str
    priority: str
    status: str
    assigned_staff_id: UUID | None
    summary: dict[str, object]
    version: int
    created_at: datetime


class TransitionRequest(BaseModel):
    version: int = Field(ge=1)
    content: str | None = Field(default=None, max_length=4000)


def _staff(principal: CurrentPrincipal) -> None:
    if principal.kind != "staff" or principal.role not in {Role.SUPPORT, Role.ADMIN}:
        raise AuthorizationError


def _response(ticket: SupportTicket) -> TicketResponse:
    return TicketResponse(
        id=ticket.id,
        conversation_id=ticket.conversation_id,
        reason_code=ticket.reason_code,
        priority=ticket.priority,
        status=ticket.status.value,
        assigned_staff_id=ticket.assigned_staff_id,
        summary=ticket.summary,
        version=ticket.version,
        created_at=ticket.created_at,
    )


@router.get("", response_model=list[TicketResponse])
async def list_queue(
    principal: CurrentPrincipal, session: DatabaseSession, status: str | None = Query(default=None)
) -> list[TicketResponse]:
    _staff(principal)
    query = select(SupportTicket).where(SupportTicket.organization_id == principal.organization_id)
    if status:
        try:
            query = query.where(SupportTicket.status == TicketStatus(status))
        except ValueError as exc:
            from fastapi import HTTPException

            raise HTTPException(status_code=422, detail="invalid_status") from exc
    items = await session.scalars(query.order_by(SupportTicket.created_at))
    return [_response(item) for item in items]


@router.get("/{ticket_id}", response_model=TicketResponse)
async def get_ticket(
    ticket_id: UUID, principal: CurrentPrincipal, session: DatabaseSession
) -> TicketResponse:
    _staff(principal)
    ticket = await session.scalar(
        select(SupportTicket).where(
            SupportTicket.organization_id == principal.organization_id,
            SupportTicket.id == ticket_id,
        )
    )
    if ticket is None:
        raise ResourceNotFoundError
    return _response(ticket)


@router.post("/{ticket_id}/{action}", response_model=TicketResponse)
async def transition(
    ticket_id: UUID,
    action: str,
    payload: TransitionRequest,
    principal: CurrentPrincipal,
    session: DatabaseSession,
    settings: RequestSettings,
    idempotency_key: str = Header(min_length=8, max_length=128, alias="Idempotency-Key"),
) -> TicketResponse:
    _staff(principal)
    if action not in {"claim", "reply", "note", "resolve", "close", "return_to_ai"}:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail="invalid_transition")
    service = HandoffService(session, settings, create_crm_provider(settings))
    try:
        ticket = await service.transition(
            organization_id=principal.organization_id,
            staff_id=principal.subject_id,
            ticket_id=ticket_id,
            expected_version=payload.version,
            action=action,
            correlation_id=idempotency_key,
            content=payload.content,
        )
    except HandoffError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail=exc.code) from exc
    await session.commit()
    return _response(ticket)
