from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


OrderStatus = Literal[
    "draft",
    "confirmed",
    "in_preparation",
    "ready",
    "delivered",
    "picked_up",
    "cancelled",
]
OrderStatusTransitionSource = Literal[
    "system",
    "operator",
]
StockReason = Literal[
    "sale",
    "restock",
    "adjustment",
    "reversal",
]

ValidationStatus = Literal[
    "ok",
    "unknown_product",
    "inactive_product",
    "insufficient_stock",
    "needs_review",
]

Weekday = Literal[
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]

FulfillmentType = Literal["delivery", "pickup"]

PaymentMethod = Literal[
    "nequi",
    "daviplata",
    "transferencia",
    "efectivo",
]

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Product(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str
    product_id: str
    product_name: str
    aliases: list[str] = Field(default_factory=list)
    category: str | None = None
    available_days: list[Weekday] | None = None
    unit: str = "unit"
    unit_price: Decimal
    active: bool = True
    current_stock: Decimal = Decimal("0")
    min_stock: Decimal = Decimal("0")
    notes: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Customer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str
    customer_id: str
    customer_name: str
    customer_phone: str | None = None
    default_address: str | None = None
    notes: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_order_at: datetime | None = None


class OrderItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str
    order_item_id: str
    order_id: str
    product_id: str | None = None

    product_name_snapshot: str
    unit_snapshot: str = "unit"

    quantity: Decimal
    unit_price_snapshot: Decimal
    line_total: Decimal

    modifications: str | None = None
    validation_status: ValidationStatus = "needs_review"
    notes: str | None = None


class Order(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str
    order_id: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    customer_id: str | None = None
    customer_name_snapshot: str | None = None
    customer_phone_snapshot: str | None = None

    raw_message: str
    status: OrderStatus = "draft"
    confirmed_at: datetime | None = None
    status_updated_at: datetime = Field(default_factory=utc_now)

    items: list[OrderItem] = Field(default_factory=list)

    subtotal: Decimal = Decimal("0")
    delivery_fee: Decimal = Decimal("0")
    packaging_fee: Decimal = Decimal("0")
    total: Decimal = Decimal("0")

    fulfillment_type: FulfillmentType | None = None
    delivery_zone: str | None = None
    customer_notes: str | None = None
    payment_method: PaymentMethod | None = None

    delivery_date: str | None = None
    delivery_address: str | None = None
    notes: str | None = None
    confirmation_message: str | None = None
    created_by: str | None = None

class OrderStatusTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transition_id: str
    tenant_id: str
    order_id: str
    from_status: OrderStatus | None = None
    to_status: OrderStatus
    occurred_at: datetime = Field(default_factory=utc_now)
    source: OrderStatusTransitionSource = "system"

class StockMovement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str
    stock_movement_id: str
    created_at: datetime = Field(default_factory=utc_now)
    

    product_id: str
    quantity_delta: Decimal
    reason: StockReason

    reference_id: str | None = None
    notes: str | None = None
    created_by: str | None = None


class DraftItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str
    product_id: str
    quantity: Decimal
    modifications: str | None = None


class DraftOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str
    raw_message: str
    customer_name: str
    customer_phone: str | None = None
    items: list[DraftItemRequest]

    fulfillment_type: FulfillmentType | None = None
    delivery_zone: str | None = None
    packaging_fee: Decimal = Decimal("0")
    customer_notes: str | None = None
    payment_method: PaymentMethod | None = None


class ParseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: DraftOrderRequest
    warnings: list[str] = []
    model: str
    latency_ms: int
    raw_response: str


class ParseLogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str
    parse_id: str
    created_at: datetime = Field(default_factory=utc_now)
    raw_message: str
    parsed_json: str
    model: str
    prompt_version: str
    latency_ms: int
    success: bool
    error: str | None = None