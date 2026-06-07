from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from duna_orders.domain.models import utc_now
from duna_orders.storage.postgres_base import Base
from duna_orders.storage.schema import (
    CUSTOMERS_TAB,
    ORDERS_TAB,
    ORDER_ITEMS_TAB,
    PARSE_LOG_TAB,
    PRODUCTS_TAB,
    STOCK_MOVEMENTS_TAB,
    PROCESSED_MESSAGES_TAB,
)


ID_LENGTH = 80
TENANT_ID_LENGTH = 120
SHORT_TEXT_LENGTH = 120
STATUS_LENGTH = 40
PHONE_LENGTH = 80


class ProductRow(Base):
    __tablename__ = PRODUCTS_TAB

    product_id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False)
    product_name: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    category: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    available_days: Mapped[list[str] | None] = mapped_column(JSON)
    unit: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False, default="unit")
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    current_stock: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False, default=0)
    min_stock: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    __table_args__ = (
        Index("ix_products_tenant_id_active", "tenant_id", "active"),
        Index("ix_products_tenant_id_category", "tenant_id", "category"),
    )


class CustomerRow(Base):
    __tablename__ = CUSTOMERS_TAB

    customer_id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False)
    customer_name: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    customer_phone: Mapped[str | None] = mapped_column(String(PHONE_LENGTH))
    default_address: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    last_order_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_customers_tenant_id_phone", "tenant_id", "customer_phone"),
    )


class OrderRow(Base):
    __tablename__ = ORDERS_TAB

    order_id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    customer_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    customer_name_snapshot: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    customer_phone_snapshot: Mapped[str | None] = mapped_column(String(PHONE_LENGTH))

    raw_message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(STATUS_LENGTH), nullable=False, default="draft")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    delivery_fee: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    packaging_fee: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)

    fulfillment_type: Mapped[str | None] = mapped_column(String(STATUS_LENGTH))
    delivery_zone: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    customer_notes: Mapped[str | None] = mapped_column(Text)
    payment_method: Mapped[str | None] = mapped_column(String(STATUS_LENGTH))

    delivery_date: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    delivery_address: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    confirmation_message: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))

    items: Mapped[list[OrderItemRow]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_orders_tenant_id_status", "tenant_id", "status"),
        Index("ix_orders_tenant_id_created_at", "tenant_id", "created_at"),
        Index("ix_orders_tenant_id_customer_id", "tenant_id", "customer_id"),
    )


class OrderItemRow(Base):
    __tablename__ = ORDER_ITEMS_TAB

    order_item_id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False)
    order_id: Mapped[str] = mapped_column(
        String(ID_LENGTH),
        ForeignKey(f"{ORDERS_TAB}.order_id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))

    product_name_snapshot: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    unit_snapshot: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False, default="unit")

    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    unit_price_snapshot: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    modifications: Mapped[str | None] = mapped_column(Text)
    validation_status: Mapped[str] = mapped_column(
        String(STATUS_LENGTH),
        nullable=False,
        default="needs_review",
    )
    notes: Mapped[str | None] = mapped_column(Text)

    order: Mapped[OrderRow] = relationship(back_populates="items")

    __table_args__ = (
        Index("ix_order_items_tenant_id_order_id", "tenant_id", "order_id"),
        Index("ix_order_items_tenant_id_product_id", "tenant_id", "product_id"),
    )


class StockMovementRow(Base):
    __tablename__ = STOCK_MOVEMENTS_TAB

    stock_movement_id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    product_id: Mapped[str] = mapped_column(String(ID_LENGTH), nullable=False)
    quantity_delta: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    reason: Mapped[str] = mapped_column(String(STATUS_LENGTH), nullable=False)

    reference_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))

    __table_args__ = (
        Index("ix_stock_movements_tenant_id_product_id", "tenant_id", "product_id"),
        Index("ix_stock_movements_tenant_id_created_at", "tenant_id", "created_at"),
        Index("ix_stock_movements_tenant_id_reference_id", "tenant_id", "reference_id"),
    )


class ParseLogRow(Base):
    __tablename__ = PARSE_LOG_TAB

    parse_id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    raw_message: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_json: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_parse_log_tenant_id_created_at", "tenant_id", "created_at"),
        Index("ix_parse_log_tenant_id_success", "tenant_id", "success"),
    )

class ProcessedMessageRow(Base):
    __tablename__ = PROCESSED_MESSAGES_TAB

    message_sid: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    from_number: Mapped[str | None] = mapped_column(String(PHONE_LENGTH))
    raw_body: Mapped[str | None] = mapped_column(Text)
    resulting_order_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))

    __table_args__ = (
        Index("ix_processed_messages_tenant_id_received_at", "tenant_id", "received_at"),
        Index("ix_processed_messages_tenant_id_resulting_order_id", "tenant_id", "resulting_order_id"),
    )