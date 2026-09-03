from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProviderContext(BaseModel):
    model_config = ConfigDict(frozen=True)
    organization_id: UUID
    actor_ref: str = Field(min_length=1, max_length=120)
    customer_ref: str | None = Field(default=None, max_length=160)
    correlation_id: str = Field(min_length=8, max_length=128)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)


class Money(BaseModel):
    amount: Decimal
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class Address(BaseModel):
    recipient: str
    line1: str
    line2: str | None = None
    city: str
    region: str
    postal_code: str
    country_code: str = Field(pattern=r"^[A-Z]{2}$")


class TrackingEvent(BaseModel):
    status: str
    occurred_at: datetime
    location: str | None = None
    detail: str | None = None


class Tracking(BaseModel):
    carrier: str
    tracking_number: str
    tracking_url: str
    estimated_delivery_at: datetime | None = None
    events: list[TrackingEvent] = Field(default_factory=list)


class LineItem(BaseModel):
    sku: str
    name: str
    quantity: int
    unit_price: Money
    final_sale: bool = False
    fulfillment_status: str


class RefundRequest(BaseModel):
    external_ref: str
    status: str
    requested_at: datetime
    amount: Money
    reason: str


class Order(BaseModel):
    external_ref: str
    order_number: str
    version: str
    status: str
    fulfillment_status: str
    provider_status: str | None = None
    placed_at: datetime
    delivered_at: datetime | None = None
    line_items: list[LineItem]
    subtotal: Money
    shipping: Money
    tax: Money
    total: Money
    shipping_address: Address
    tracking: Tracking | None = None
    refund_requests: list[RefundRequest] = Field(default_factory=list)


class Contact(BaseModel):
    external_ref: str
    email: str
    first_name: str
    last_name: str
    lifecycle_stage: str
    locale: str | None = None
    company: str | None = None
    provider_status: str | None = None
    version: str
    created_at: datetime
    updated_at: datetime


class ContactUpsert(BaseModel):
    email: str
    first_name: str
    last_name: str
    lifecycle_stage: str = "lead"
    locale: str | None = None
    company: str | None = None


class ConversationNote(BaseModel):
    external_ref: str
    contact_ref: str
    body: str
    created_at: datetime


class ProviderErrorCode(str, Enum):
    NOT_FOUND = "not_found"
    NOT_AUTHORIZED = "not_authorized"
    CONFLICT = "conflict"
    VALIDATION = "validation"
    UNSUPPORTED = "unsupported"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
