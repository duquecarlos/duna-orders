from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


OrderStatus = Literal[
    "draft",
    "reviewed",
    "confirmed",
    "prepared",
    "delivered",
    "cancelled",
]

StockReason = Literal[
    "sale",
    "restock",
    "manual_adjustment",
    "correction",
    "cancelled_order_reversal",
    "reversal",
]

ValidationStatus = Literal[
    "ok",
    "unknown_product",
    "inactive_product",
    "insufficient_stock",
    "needs_review",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Product(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str
    product_name: str
    aliases: list[str] = Field(default_factory=list)
    category: str | None = None
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

    order_item_id: str
    order_id: str
    product_id: str | None = None

    product_name_snapshot: str
    unit_snapshot: str = "unit"

    quantity: Decimal
    unit_price_snapshot: Decimal
    line_total: Decimal

    validation_status: ValidationStatus = "needs_review"
    notes: str | None = None


class Order(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    customer_id: str | None = None
    customer_name_snapshot: str | None = None
    customer_phone_snapshot: str | None = None

    raw_message: str
    status: OrderStatus = "draft"
    confirmed_at: datetime | None = None

    items: list[OrderItem] = Field(default_factory=list)

    subtotal: Decimal = Decimal("0")
    delivery_fee: Decimal = Decimal("0")
    total: Decimal = Decimal("0")

    delivery_date: str | None = None
    delivery_address: str | None = None
    notes: str | None = None
    confirmation_message: str | None = None
    created_by: str | None = None


class StockMovement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stock_movement_id: str
    created_at: datetime = Field(default_factory=utc_now)

    product_id: str
    quantity_delta: Decimal
    reason: StockReason

    related_order_id: str | None = None
    notes: str | None = None
    created_by: str | None = None