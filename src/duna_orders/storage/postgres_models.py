from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from duna_orders.domain.models import utc_now
from duna_orders.storage.postgres_base import Base
from duna_orders.storage.schema import (
    CUSTOMERS_TAB,
    ORDERS_TAB,
    ORDER_ITEMS_TAB,
    ORDER_STATUS_TRANSITIONS_TAB,
    OUTBOUND_MESSAGES_TAB,
    PARSE_LOG_TAB,
    PRODUCTS_TAB,
    STOCK_MOVEMENTS_TAB,
    PROCESSED_MESSAGES_TAB,
    CONVERSATION_SESSIONS_TAB,
    CONVERSATION_TURNS_TAB,
    CONVERSATION_CUSTOMER_CLAIMS_TAB,
    DEFERRED_INBOUND_TAB,
    CONVERSATION_ACCUMULATED_DRAFTS_TAB,
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
    conversation_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))

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
        Index(
            "uq_orders_conversation_id_not_null",
            "conversation_id",
            unique=True,
            postgresql_where=(conversation_id.is_not(None)),
            sqlite_where=(conversation_id.is_not(None)),
        ),
        Index("ix_orders_tenant_id_conversation_id", "tenant_id", "conversation_id"),
        Index("ix_orders_tenant_id_status", "tenant_id", "status"),
        Index("ix_orders_tenant_id_created_at", "tenant_id", "created_at"),
        Index("ix_orders_tenant_id_customer_id", "tenant_id", "customer_id"),
    )

class OrderStatusTransitionRow(Base):
    __tablename__ = ORDER_STATUS_TRANSITIONS_TAB

    transition_id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False)
    order_id: Mapped[str] = mapped_column(
        String(ID_LENGTH),
        ForeignKey(f"{ORDERS_TAB}.order_id", ondelete="CASCADE"),
        nullable=False,
    )
    from_status: Mapped[str | None] = mapped_column(String(STATUS_LENGTH))
    to_status: Mapped[str] = mapped_column(String(STATUS_LENGTH), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    source: Mapped[str] = mapped_column(
        String(STATUS_LENGTH),
        nullable=False,
        default="system",
    )

    __table_args__ = (
        Index(
            "ix_order_status_transitions_tenant_id_order_id",
            "tenant_id",
            "order_id",
        ),
        Index(
            "ix_order_status_transitions_tenant_id_occurred_at",
            "tenant_id",
            "occurred_at",
        ),
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


class OutboundMessageRow(Base):
    __tablename__ = OUTBOUND_MESSAGES_TAB

    outbound_message_id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False)
    order_id: Mapped[str] = mapped_column(String(ID_LENGTH), nullable=False)
    acknowledgement_type: Mapped[str] = mapped_column(String(STATUS_LENGTH), nullable=False)
    to_number: Mapped[str] = mapped_column(String(PHONE_LENGTH), nullable=False)
    from_number: Mapped[str] = mapped_column(String(PHONE_LENGTH), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(STATUS_LENGTH), nullable=False)
    provider: Mapped[str] = mapped_column(String(STATUS_LENGTH), nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(SHORT_TEXT_LENGTH))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[str] = mapped_column(String(SHORT_TEXT_LENGTH), nullable=False)
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
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "order_id",
            "acknowledgement_type",
            name="uq_outbound_messages_tenant_order_ack_type",
        ),
        Index("ix_outbound_messages_tenant_id_order_id", "tenant_id", "order_id"),
        Index("ix_outbound_messages_tenant_id_status", "tenant_id", "status"),
        Index("ix_outbound_messages_tenant_id_created_at", "tenant_id", "created_at"),
    )


class ConversationSessionRow(Base):
    __tablename__ = CONVERSATION_SESSIONS_TAB

    conversation_id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False)
    customer_phone: Mapped[str] = mapped_column(String(PHONE_LENGTH), nullable=False)
    status: Mapped[str] = mapped_column(String(STATUS_LENGTH), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    resulting_order_id: Mapped[str | None] = mapped_column(String(ID_LENGTH))
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
    latest_advancement_outcome: Mapped[str | None] = mapped_column(String(STATUS_LENGTH))
    latest_parse_error_category: Mapped[str | None] = mapped_column(String(STATUS_LENGTH))

    turns: Mapped[list[ConversationTurnRow]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index(
            "uq_conversation_sessions_one_open_per_customer",
            "tenant_id",
            "customer_phone",
            unique=True,
            postgresql_where=(status == "open"),
            sqlite_where=(status == "open"),
        ),
        Index(
            "ix_conversation_sessions_tenant_id_customer_phone",
            "tenant_id",
            "customer_phone",
        ),
        Index("ix_conversation_sessions_tenant_id_status", "tenant_id", "status"),
    )


class ConversationTurnRow(Base):
    __tablename__ = CONVERSATION_TURNS_TAB

    turn_id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(ID_LENGTH),
        ForeignKey(f"{CONVERSATION_SESSIONS_TAB}.conversation_id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False)
    message_sid: Mapped[str] = mapped_column(String(ID_LENGTH), nullable=False)
    from_number: Mapped[str | None] = mapped_column(String(PHONE_LENGTH))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    session: Mapped[ConversationSessionRow] = relationship(back_populates="turns")

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "message_sid",
            name="uq_conversation_turns_tenant_message_sid",
        ),
        Index(
            "ix_conversation_turns_tenant_id_conversation_id",
            "tenant_id",
            "conversation_id",
        ),
        Index(
            "ix_conversation_turns_conversation_sequence",
            "conversation_id",
            "sequence_number",
        ),
    )


class ConversationCustomerClaimRow(Base):
    __tablename__ = CONVERSATION_CUSTOMER_CLAIMS_TAB

    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), primary_key=True)
    customer_key: Mapped[str] = mapped_column(String(PHONE_LENGTH), primary_key=True)
    holder_id: Mapped[str] = mapped_column(String(ID_LENGTH), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConversationAccumulatedDraftRow(Base):
    __tablename__ = CONVERSATION_ACCUMULATED_DRAFTS_TAB

    conversation_id: Mapped[str] = mapped_column(
        String(ID_LENGTH),
        ForeignKey(
            f"{CONVERSATION_SESSIONS_TAB}.conversation_id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False)
    accumulated_json: Mapped[str] = mapped_column(Text, nullable=False)
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index(
            "ix_conversation_accumulated_drafts_tenant_id_conversation_id",
            "tenant_id",
            "conversation_id",
        ),
    )


class DeferredInboundRow(Base):
    __tablename__ = DEFERRED_INBOUND_TAB

    message_sid: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False)
    customer_key: Mapped[str] = mapped_column(String(PHONE_LENGTH), nullable=False)
    from_number: Mapped[str] = mapped_column(String(PHONE_LENGTH), nullable=False)
    raw_body: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deferred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index(
            "ix_deferred_inbound_pending_by_customer",
            "tenant_id",
            "customer_key",
            "received_at",
            "deferred_at",
            "message_sid",
            postgresql_where=(processed_at.is_(None)),
            sqlite_where=(processed_at.is_(None)),
        ),
    )
