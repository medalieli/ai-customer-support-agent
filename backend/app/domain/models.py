import enum
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Status(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class Role(str, enum.Enum):
    SUPPORT = "support"
    ADMIN = "admin"


class ConversationStatus(str, enum.Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    CLOSED = "closed"


class ConversationOwner(str, enum.Enum):
    AI = "ai"
    STAFF = "staff"


class MessageRole(str, enum.Enum):
    CUSTOMER = "customer"
    ASSISTANT = "assistant"
    STAFF = "staff"
    SYSTEM_EVENT = "system_event"


class RecordStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TicketStatus(str, enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"


class WebhookStatus(str, enum.Enum):
    RECEIVED = "received"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"
    CONFLICT = "conflict"
    STALE = "stale"


class DocumentStatus(str, enum.Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    ARCHIVED = "archived"


class IngestionStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[Status] = mapped_column(Enum(Status, native_enum=False), default=Status.ACTIVE)
    default_locale: Mapped[str] = mapped_column(String(5), default="en")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")


class Customer(Base, TimestampMixin):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "demo_key"),
        UniqueConstraint("organization_id", "provider", "provider_customer_ref"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    demo_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    display_name: Mapped[str] = mapped_column(String(160))
    email: Mapped[str] = mapped_column(String(320))
    locale: Mapped[str] = mapped_column(String(5), default="en")
    provider: Mapped[str] = mapped_column(String(40), default="mock")
    provider_customer_ref: Mapped[str] = mapped_column(String(160))
    status: Mapped[Status] = mapped_column(Enum(Status, native_enum=False), default=Status.ACTIVE)


class StaffUser(Base, TimestampMixin):
    __tablename__ = "staff_users"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(160))
    password_hash: Mapped[str] = mapped_column(String(512))
    status: Mapped[Status] = mapped_column(Enum(Status, native_enum=False), default=Status.ACTIVE)


class OrganizationMembership(Base, TimestampMixin):
    __tablename__ = "organization_memberships"
    __table_args__ = (UniqueConstraint("organization_id", "staff_user_id"),)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    staff_user_id: Mapped[UUID] = mapped_column(ForeignKey("staff_users.id"))
    role: Mapped[Role] = mapped_column(Enum(Role, native_enum=False))
    status: Mapped[Status] = mapped_column(Enum(Status, native_enum=False), default=Status.ACTIVE)


class CustomerSession(Base):
    __tablename__ = "customer_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "customer_id"], ["customers.organization_id", "customers.id"]
        ),
        UniqueConstraint("organization_id", "token_hash"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    customer_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StaffSession(Base):
    __tablename__ = "staff_sessions"
    __table_args__ = (UniqueConstraint("organization_id", "token_hash"),)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    staff_user_id: Mapped[UUID] = mapped_column(ForeignKey("staff_users.id"))
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        ForeignKeyConstraint(
            ["organization_id", "customer_id"], ["customers.organization_id", "customers.id"]
        ),
        ForeignKeyConstraint(
            ["organization_id", "assigned_staff_id"],
            ["organization_memberships.organization_id", "organization_memberships.staff_user_id"],
        ),
        Index("ix_conversations_customer_created", "organization_id", "customer_id", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    customer_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    title: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[ConversationStatus] = mapped_column(
        Enum(ConversationStatus, native_enum=False), default=ConversationStatus.OPEN
    )
    owner: Mapped[ConversationOwner] = mapped_column(
        Enum(ConversationOwner, native_enum=False), default=ConversationOwner.AI
    )
    assigned_staff_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    locale: Mapped[str] = mapped_column(String(5), default="en")
    ownership_state: Mapped[str] = mapped_column(String(30), default="ai_active")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
        ),
        ForeignKeyConstraint(
            ["organization_id", "sender_customer_id"],
            ["customers.organization_id", "customers.id"],
        ),
        ForeignKeyConstraint(
            ["organization_id", "sender_staff_id"],
            ["organization_memberships.organization_id", "organization_memberships.staff_user_id"],
        ),
        UniqueConstraint("conversation_id", "sequence_number"),
        Index(
            "ix_messages_conversation_sequence",
            "organization_id",
            "conversation_id",
            "sequence_number",
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    conversation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    sequence_number: Mapped[int] = mapped_column(Integer)
    role: Mapped[MessageRole] = mapped_column(Enum(MessageRole, native_enum=False))
    sender_customer_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    sender_staff_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    content: Mapped[str] = mapped_column(Text)
    locale: Mapped[str] = mapped_column(String(5))
    visible_to_customer: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentThread(Base, TimestampMixin):
    __tablename__ = "agent_threads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
        ),
        UniqueConstraint("organization_id", "conversation_id"),
        UniqueConstraint("organization_id", "id"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    conversation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    checkpoint_thread_id: Mapped[str] = mapped_column(String(160), unique=True)
    checkpoint_version: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(40), default="idle")
    interrupt_reason: Mapped[str | None] = mapped_column(String(80))


class AgentRun(Base, TimestampMixin):
    __tablename__ = "agent_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "thread_id"],
            ["agent_threads.organization_id", "agent_threads.id"],
        ),
        UniqueConstraint("organization_id", "thread_id", "idempotency_key"),
        UniqueConstraint("organization_id", "id"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    thread_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(40), default="running")
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AgentEvent(Base):
    __tablename__ = "agent_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "run_id"], ["agent_runs.organization_id", "agent_runs.id"]
        ),
        UniqueConstraint("run_id", "sequence_number"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    sequence_number: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ToolRun(Base, TimestampMixin):
    __tablename__ = "tool_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    conversation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    tool_name: Mapped[str] = mapped_column(String(120))
    schema_version: Mapped[str] = mapped_column(String(40))
    risk: Mapped[str] = mapped_column(String(40))
    status: Mapped[RecordStatus] = mapped_column(Enum(RecordStatus, native_enum=False))
    request_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class PendingAction(Base, TimestampMixin):
    __tablename__ = "pending_actions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
        ),
        ForeignKeyConstraint(
            ["organization_id", "customer_id"], ["customers.organization_id", "customers.id"]
        ),
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "idempotency_key"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    conversation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    customer_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    action_type: Mapped[str] = mapped_column(String(80))
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    session_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    order_ref: Mapped[str] = mapped_column(String(160))
    order_number: Mapped[str] = mapped_column(String(40))
    order_version: Mapped[str] = mapped_column(String(80))
    encrypted_payload: Mapped[bytes] = mapped_column(LargeBinary)
    payload_hash: Mapped[str] = mapped_column(String(64))
    action_hash: Mapped[str] = mapped_column(String(64))
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[RecordStatus] = mapped_column(Enum(RecordStatus, native_enum=False))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_version: Mapped[str | None] = mapped_column(String(80))
    failure_code: Mapped[str | None] = mapped_column(String(80))


class ConsentRecord(Base):
    __tablename__ = "consent_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "customer_id"], ["customers.organization_id", "customers.id"]
        ),
        ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
        ),
        ForeignKeyConstraint(
            ["organization_id", "pending_action_id"],
            ["pending_actions.organization_id", "pending_actions.id"],
        ),
        UniqueConstraint("organization_id", "pending_action_id"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    customer_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    conversation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    pending_action_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    purpose: Mapped[str] = mapped_column(String(80))
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RefundDecisionRecord(Base):
    """Immutable inputs/result for a deterministic refund evaluation."""

    __tablename__ = "refund_decisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
        ),
        ForeignKeyConstraint(
            ["organization_id", "customer_id"], ["customers.organization_id", "customers.id"]
        ),
        ForeignKeyConstraint(
            ["organization_id", "policy_document_id"],
            ["knowledge_documents.organization_id", "knowledge_documents.id"],
        ),
        ForeignKeyConstraint(
            ["organization_id", "policy_version_id"],
            ["document_versions.organization_id", "document_versions.id"],
        ),
        UniqueConstraint("organization_id", "id"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    conversation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    customer_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    order_ref: Mapped[str] = mapped_column(String(160))
    order_number: Mapped[str] = mapped_column(String(40))
    order_version: Mapped[str] = mapped_column(String(80))
    policy_document_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    policy_version_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    policy_version: Mapped[str | None] = mapped_column(String(80))
    policy_fingerprint: Mapped[str | None] = mapped_column(String(64))
    ruleset_version: Mapped[str] = mapped_column(String(40))
    outcome: Mapped[str] = mapped_column(String(40))
    reason_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    facts_hash: Mapped[str] = mapped_column(String(64))
    citation_receipt_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    state: Mapped[str] = mapped_column(String(40), default="evaluated")
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SupportTicket(Base, TimestampMixin):
    __tablename__ = "support_tickets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
        ),
        ForeignKeyConstraint(
            ["organization_id", "assigned_staff_id"],
            ["organization_memberships.organization_id", "organization_memberships.staff_user_id"],
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    conversation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    assigned_staff_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    reason_code: Mapped[str] = mapped_column(String(80))
    priority: Mapped[str] = mapped_column(String(20))
    status: Mapped[TicketStatus] = mapped_column(Enum(TicketStatus, native_enum=False))
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    provider_ref: Mapped[str | None] = mapped_column(String(160))
    version: Mapped[int] = mapped_column(Integer, default=1)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    actor_type: Mapped[str] = mapped_column(String(30))
    actor_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(100), index=True)
    target_type: Mapped[str | None] = mapped_column(String(50))
    target_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    outcome: Mapped[str] = mapped_column(String(30))
    reason_code: Mapped[str | None] = mapped_column(String(80))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint("connection_id", "external_event_id"),
        ForeignKeyConstraint(
            ["organization_id", "connection_id"],
            ["provider_connections.organization_id", "provider_connections.id"],
        ),
        Index("ix_webhook_claim", "status", "next_attempt_at", "received_at"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    connection_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    external_event_id: Mapped[str] = mapped_column(String(200))
    topic: Mapped[str] = mapped_column(String(120))
    payload_hash: Mapped[str] = mapped_column(String(64))
    encrypted_payload: Mapped[bytes] = mapped_column(LargeBinary)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[WebhookStatus] = mapped_column(Enum(WebhookStatus, native_enum=False))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    safe_error: Mapped[str | None] = mapped_column(String(120))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ProviderConnection(Base, TimestampMixin):
    __tablename__ = "provider_connections"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("provider", "endpoint_key"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    endpoint_key: Mapped[str] = mapped_column(String(120))
    encrypted_current_secret: Mapped[bytes] = mapped_column(LargeBinary)
    encrypted_previous_secret: Mapped[bytes | None] = mapped_column(LargeBinary)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ProviderProjection(Base, TimestampMixin):
    __tablename__ = "provider_projections"
    __table_args__ = (
        UniqueConstraint("organization_id", "provider", "resource_type", "external_ref"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    resource_type: Mapped[str] = mapped_column(String(40))
    external_ref: Mapped[str] = mapped_column(String(160))
    version: Mapped[str] = mapped_column(String(80))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    safe_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ProviderResourceBinding(Base, TimestampMixin):
    __tablename__ = "provider_resource_bindings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "connection_id"],
            ["provider_connections.organization_id", "provider_connections.id"],
        ),
        ForeignKeyConstraint(
            ["organization_id", "customer_id"], ["customers.organization_id", "customers.id"]
        ),
        ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
        ),
        UniqueConstraint("connection_id", "resource_type", "external_ref", "conversation_id"),
        UniqueConstraint("organization_id", "id"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    connection_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    resource_type: Mapped[str] = mapped_column(String(40))
    external_ref: Mapped[str] = mapped_column(String(160))
    customer_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    conversation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    provider_customer_ref: Mapped[str | None] = mapped_column(String(160))


class WebhookConversationEffect(Base):
    __tablename__ = "webhook_conversation_effects"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "webhook_event_id"],
            ["webhook_events.organization_id", "webhook_events.id"],
        ),
        ForeignKeyConstraint(
            ["organization_id", "binding_id"],
            ["provider_resource_bindings.organization_id", "provider_resource_bindings.id"],
        ),
        ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
        ),
        UniqueConstraint("webhook_event_id", "binding_id"),
        UniqueConstraint("binding_id", "logical_key"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    webhook_event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    binding_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    conversation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    logical_key: Mapped[str] = mapped_column(String(64))
    message_id: Mapped[UUID | None] = mapped_column(ForeignKey("messages.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeDocument(Base, TimestampMixin):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "slug"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    slug: Mapped[str] = mapped_column(String(160))
    title: Mapped[str] = mapped_column(String(240), default="Untitled")
    document_type: Mapped[str] = mapped_column(String(50))
    status: Mapped[DocumentStatus] = mapped_column(Enum(DocumentStatus, native_enum=False))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentVersion(Base, TimestampMixin):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("document_id", "version", "locale"),
        ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["knowledge_documents.organization_id", "knowledge_documents.id"],
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    document_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    version: Mapped[str] = mapped_column(String(80))
    locale: Mapped[str] = mapped_column(String(5))
    checksum: Mapped[str] = mapped_column(String(64))
    status: Mapped[IngestionStatus] = mapped_column(
        Enum(IngestionStatus, native_enum=False), default=IngestionStatus.PENDING
    )
    source_filename: Mapped[str] = mapped_column(String(240))
    media_type: Mapped[str] = mapped_column(String(100))
    raw_content: Mapped[bytes] = mapped_column(LargeBinary)
    error_code: Mapped[str | None] = mapped_column(String(80))
    embedding_provider: Mapped[str | None] = mapped_column(String(40))
    embedding_model: Mapped[str | None] = mapped_column(String(160))
    embedding_dimension: Mapped[int | None] = mapped_column(Integer)
    indexing_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChunkMetadata(Base):
    __tablename__ = "chunk_metadata"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_chunk_metadata_org_id"),
        UniqueConstraint("document_version_id", "ordinal"),
        Index("ix_chunk_metadata_search_vector", "search_vector", postgresql_using="gin"),
        Index(
            "ix_chunk_metadata_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        ForeignKeyConstraint(
            ["organization_id", "document_version_id"],
            ["document_versions.organization_id", "document_versions.id"],
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    document_version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    ordinal: Mapped[int] = mapped_column(Integer)
    token_count: Mapped[int] = mapped_column(Integer)
    checksum: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(5))
    page_number: Mapped[int | None] = mapped_column(Integer)
    section_anchor: Mapped[str | None] = mapped_column(String(300))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))
    indexing_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    search_vector: Mapped[Any] = mapped_column(TSVECTOR)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class CitationRecord(Base):
    __tablename__ = "citation_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["knowledge_documents.organization_id", "knowledge_documents.id"],
        ),
        ForeignKeyConstraint(
            ["organization_id", "document_version_id"],
            ["document_versions.organization_id", "document_versions.id"],
        ),
        ForeignKeyConstraint(
            ["organization_id", "chunk_id"],
            ["chunk_metadata.organization_id", "chunk_metadata.id"],
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    document_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    document_version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    chunk_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    source_title: Mapped[str] = mapped_column(String(240))
    language: Mapped[str] = mapped_column(String(5))
    section_anchor: Mapped[str | None] = mapped_column(String(300))
    page_number: Mapped[int | None] = mapped_column(Integer)
    snippet: Mapped[str] = mapped_column(Text)
    chunk_checksum: Mapped[str] = mapped_column(String(64))
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
