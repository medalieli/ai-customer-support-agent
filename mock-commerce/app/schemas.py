from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class Money(BaseModel):
    amount: Decimal = Field(decimal_places=2)
    currency: str = Field(pattern="^[A-Z]{3}$")


class Address(BaseModel):
    recipient: str = Field(min_length=1, max_length=120)
    line1: str = Field(min_length=1, max_length=160)
    line2: str | None = Field(default=None, max_length=160)
    city: str = Field(min_length=1, max_length=100)
    region: str = Field(min_length=1, max_length=100)
    postal_code: str = Field(min_length=2, max_length=20)
    country_code: str = Field(pattern="^[A-Z]{2}$")


class LineItem(BaseModel):
    sku: str
    name: str
    quantity: int = Field(ge=1)
    unit_price: Money
    final_sale: bool = False
    fulfillment_status: Literal["unfulfilled", "fulfilled", "cancelled"]


class FulfillmentEvent(BaseModel):
    status: str
    occurred_at: datetime
    location: str | None = None
    detail: str | None = None


class Tracking(BaseModel):
    carrier: str
    tracking_number: str
    tracking_url: str
    estimated_delivery_at: datetime | None = None
    events: list[FulfillmentEvent]


class RefundRequest(BaseModel):
    external_ref: str
    status: Literal["requested", "reviewing", "approved", "declined", "refunded"]
    requested_at: datetime
    amount: Money
    reason: str


class Order(BaseModel):
    external_ref: str
    order_number: str
    version: int
    status: Literal["open", "cancelled", "refunded"]
    fulfillment_status: Literal[
        "unfulfilled", "partially_fulfilled", "shipped", "delayed", "delivered", "cancelled"
    ]
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


class OrderSummary(BaseModel):
    external_ref: str
    order_number: str
    version: int
    status: str
    fulfillment_status: str
    placed_at: datetime
    total: Money


class OrderPage(BaseModel):
    items: list[OrderSummary]
    next_cursor: str | None


class AddressUpdate(BaseModel):
    address: Address


class RefundCreate(BaseModel):
    amount: Money
    reason: str = Field(min_length=3, max_length=500)


class ErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool


class ErrorResponse(BaseModel):
    error: ErrorDetail
