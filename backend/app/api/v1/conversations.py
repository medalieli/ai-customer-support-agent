from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.dependencies import CurrentPrincipal, DatabaseSession
from app.domain.models import Conversation, Customer, Message, MessageRole
from app.repositories.conversations import ConversationRepository
from app.services.audit import AuditService
from app.services.auth import AuthorizationError, ResourceNotFoundError

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: UUID, principal: CurrentPrincipal, session: DatabaseSession
) -> None:
    if principal.kind != "customer":
        raise AuthorizationError
    conversation = await authorized_conversation(
        ConversationRepository(session), principal, conversation_id, session
    )
    conversation.customer_deleted_at = datetime.now(timezone.utc)
    await AuditService(session).record(
        principal.organization_id,
        "customer",
        "conversation.delete",
        "success",
        actor_id=principal.subject_id,
        target_type="conversation",
        target_id=conversation.id,
    )
    await session.commit()


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    locale: str | None = Field(default=None, pattern="^(en|fr)$")
    customer_id: UUID | None = None
    organization_id: UUID | None = None


class ConversationResponse(BaseModel):
    id: UUID
    status: str
    owner: str
    ownership_state: str
    title: str | None
    locale: str
    created_at: datetime


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=10000)
    customer_id: UUID | None = None
    organization_id: UUID | None = None


class MessageResponse(BaseModel):
    id: UUID
    sequence_number: int
    role: str
    content: str
    locale: str
    created_at: datetime


def conversation_response(item: Conversation) -> ConversationResponse:
    return ConversationResponse(
        id=item.id,
        status=item.status.value,
        owner=item.owner.value,
        ownership_state=item.ownership_state,
        title=item.title,
        locale=item.locale,
        created_at=item.created_at,
    )


def message_response(item: Message) -> MessageResponse:
    return MessageResponse(
        id=item.id,
        sequence_number=item.sequence_number,
        role=item.role.value,
        content=item.content,
        locale=item.locale,
        created_at=item.created_at,
    )


async def authorized_conversation(
    repository: ConversationRepository,
    principal: CurrentPrincipal,
    conversation_id: UUID,
    session: DatabaseSession,
) -> Conversation:
    if principal.kind == "customer":
        conversation = await repository.get_for_customer(
            principal.organization_id, principal.subject_id, conversation_id
        )
    else:
        conversation = await repository.get_for_staff(principal.organization_id, conversation_id)
    if conversation is None:
        await AuditService(session).record(
            principal.organization_id,
            principal.kind,
            "authorization.denied",
            "denied",
            actor_id=principal.subject_id,
            target_type="conversation",
            target_id=conversation_id,
            reason_code="NOT_FOUND_OR_NOT_OWNED",
        )
        await session.commit()
        raise ResourceNotFoundError
    return conversation


@router.post("", response_model=ConversationResponse, status_code=201)
async def create_conversation(
    payload: ConversationCreate, principal: CurrentPrincipal, session: DatabaseSession
) -> ConversationResponse:
    if principal.kind != "customer":
        raise AuthorizationError
    repository = ConversationRepository(session)
    locale = payload.locale
    if locale is None:
        locale = await session.scalar(
            select(Customer.locale).where(
                Customer.organization_id == principal.organization_id,
                Customer.id == principal.subject_id,
            )
        )
    conversation = await repository.create_for_customer(
        principal.organization_id, principal.subject_id, locale or "en", payload.title
    )
    await AuditService(session).record(
        principal.organization_id,
        "customer",
        "conversation.create",
        "success",
        actor_id=principal.subject_id,
        target_type="conversation",
        target_id=conversation.id,
    )
    await session.commit()
    return conversation_response(conversation)


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(
    principal: CurrentPrincipal,
    session: DatabaseSession,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[ConversationResponse]:
    if principal.kind != "customer":
        raise AuthorizationError
    items = await ConversationRepository(session).list_for_customer(
        principal.organization_id, principal.subject_id, limit, offset
    )
    return [conversation_response(item) for item in items]


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: UUID, principal: CurrentPrincipal, session: DatabaseSession
) -> ConversationResponse:
    repository = ConversationRepository(session)
    conversation = await authorized_conversation(repository, principal, conversation_id, session)
    if principal.kind == "staff":
        await AuditService(session).record(
            principal.organization_id,
            "staff",
            "conversation.access",
            "success",
            actor_id=principal.subject_id,
            target_type="conversation",
            target_id=conversation.id,
            metadata={"role": principal.role.value if principal.role else "none"},
        )
        await session.commit()
    return conversation_response(conversation)


@router.post("/{conversation_id}/messages", response_model=MessageResponse, status_code=201)
async def create_message(
    conversation_id: UUID,
    payload: MessageCreate,
    principal: CurrentPrincipal,
    session: DatabaseSession,
) -> MessageResponse:
    repository = ConversationRepository(session)
    conversation = await authorized_conversation(repository, principal, conversation_id, session)
    role = MessageRole.CUSTOMER if principal.kind == "customer" else MessageRole.STAFF
    message = await repository.add_message(
        conversation,
        role,
        payload.content,
        conversation.locale,
        sender_customer_id=principal.subject_id if principal.kind == "customer" else None,
        sender_staff_id=principal.subject_id if principal.kind == "staff" else None,
    )
    await session.commit()
    return message_response(message)


@router.get("/{conversation_id}/messages", response_model=list[MessageResponse])
async def list_messages(
    conversation_id: UUID,
    principal: CurrentPrincipal,
    session: DatabaseSession,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[MessageResponse]:
    repository = ConversationRepository(session)
    conversation = await authorized_conversation(repository, principal, conversation_id, session)
    messages = await repository.list_messages(conversation, after_sequence, limit)
    return [
        message_response(item)
        for item in messages
        if item.visible_to_customer or principal.kind == "staff"
    ]
