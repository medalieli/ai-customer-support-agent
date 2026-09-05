from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select

from app.api.dependencies import CurrentPrincipal, DatabaseSession, RequestSettings
from app.domain.models import (
    AuditEvent,
    Conversation,
    PendingAction,
    RefundDecisionRecord,
    Role,
    SupportTicket,
    TicketStatus,
    WebhookConversationEffect,
)
from app.providers.factory import create_crm_provider
from app.services.auth import AuthorizationError, ResourceNotFoundError
from app.services.handoff import HandoffError, HandoffService

router = APIRouter(prefix="/staff/tickets", tags=["staff-tickets"])
conversation_router = APIRouter(prefix="/staff/conversations", tags=["staff-audit"])


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


class AuditTimelineItem(BaseModel):
    id: UUID
    category: str
    action: str
    outcome: str
    reason_code: str | None
    actor_type: str
    occurred_at: datetime
    metadata: dict[str, object]


class AuditTimelinePage(BaseModel):
    items: list[AuditTimelineItem]
    next_cursor: UUID | None


def _audit_category(action: str) -> str:
    if action == "agent.triage_completed":
        return "triage"
    if action.startswith("agent.tool_"):
        return "tool"
    prefixes = {
        "tool": "tool",
        "address": "confirmation",
        "refund": "confirmation",
        "sales_lead": "crm",
        "crm": "crm",
        "handoff": "escalation",
        "ticket": "ticket",
        "staff": "staff",
        "webhook": "webhook",
        "agent": "ai",
        "ai": "ai",
    }
    return next(
        (value for prefix, value in prefixes.items() if action.startswith(prefix)), "system"
    )


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


@router.get("/{ticket_id}/audit", response_model=AuditTimelinePage)
async def ticket_audit_timeline(
    ticket_id: UUID,
    principal: CurrentPrincipal,
    session: DatabaseSession,
    cursor: UUID | None = None,
    limit: int = Query(default=25, ge=1, le=100),
) -> AuditTimelinePage:
    """Return a sanitized, tenant-scoped operational timeline for a ticket."""
    _staff(principal)
    ticket = await session.scalar(
        select(SupportTicket).where(
            SupportTicket.organization_id == principal.organization_id,
            SupportTicket.id == ticket_id,
        )
    )
    if ticket is None:
        raise ResourceNotFoundError
    return await conversation_audit_timeline(
        ticket.conversation_id, principal, session, cursor, limit
    )


@conversation_router.get("/{conversation_id}/audit", response_model=AuditTimelinePage)
async def conversation_audit_timeline(
    conversation_id: UUID,
    principal: CurrentPrincipal,
    session: DatabaseSession,
    cursor: UUID | None = None,
    limit: int = Query(default=25, ge=1, le=100),
) -> AuditTimelinePage:
    _staff(principal)
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.organization_id == principal.organization_id,
            Conversation.id == conversation_id,
        )
    )
    if conversation is None:
        raise ResourceNotFoundError

    scope = or_(
        and_(
            AuditEvent.target_type.in_(("ticket", "support_ticket")),
            AuditEvent.target_id.in_(
                select(SupportTicket.id).where(
                    SupportTicket.organization_id == principal.organization_id,
                    SupportTicket.conversation_id == conversation_id,
                )
            ),
        ),
        and_(
            AuditEvent.target_type == "conversation",
            AuditEvent.target_id == conversation_id,
        ),
        and_(
            AuditEvent.target_type == "pending_action",
            AuditEvent.target_id.in_(
                select(PendingAction.id).where(
                    PendingAction.organization_id == principal.organization_id,
                    PendingAction.conversation_id == conversation_id,
                )
            ),
        ),
        and_(
            AuditEvent.target_type == "refund_decision",
            AuditEvent.target_id.in_(
                select(RefundDecisionRecord.id).where(
                    RefundDecisionRecord.organization_id == principal.organization_id,
                    RefundDecisionRecord.conversation_id == conversation_id,
                )
            ),
        ),
        and_(
            AuditEvent.target_type == "webhook",
            AuditEvent.target_id.in_(
                select(WebhookConversationEffect.webhook_event_id).where(
                    WebhookConversationEffect.organization_id == principal.organization_id,
                    WebhookConversationEffect.conversation_id == conversation_id,
                )
            ),
        ),
    )
    query = select(AuditEvent).where(AuditEvent.organization_id == principal.organization_id, scope)
    if cursor is not None:
        cursor_event = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.organization_id == principal.organization_id,
                AuditEvent.id == cursor,
                scope,
            )
        )
        if cursor_event is None:
            raise ResourceNotFoundError
        query = query.where(
            or_(
                AuditEvent.occurred_at < cursor_event.occurred_at,
                and_(
                    AuditEvent.occurred_at == cursor_event.occurred_at,
                    AuditEvent.id < cursor_event.id,
                ),
            )
        )
    events = list(
        await session.scalars(
            query.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc()).limit(limit + 1)
        )
    )
    has_more = len(events) > limit
    events = events[:limit]
    return AuditTimelinePage(
        items=[
            AuditTimelineItem(
                id=event.id,
                category=_audit_category(event.action),
                action=event.action,
                outcome=event.outcome,
                reason_code=event.reason_code,
                actor_type=event.actor_type,
                occurred_at=event.occurred_at,
                metadata={},
            )
            for event in events
        ],
        next_cursor=events[-1].id if has_more else None,
    )


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
