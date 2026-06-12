PRODUCTS_TAB = "products"
CUSTOMERS_TAB = "customers"
ORDERS_TAB = "orders"
ORDER_ITEMS_TAB = "order_items"
STOCK_MOVEMENTS_TAB = "stock_movements"
PARSE_LOG_TAB = "parse_log"
PROCESSED_MESSAGES_TAB = "processed_messages"
ORDER_STATUS_TRANSITIONS_TAB = "order_status_transitions"
OUTBOUND_MESSAGES_TAB = "outbound_messages"
CONVERSATION_SESSIONS_TAB = "conversation_sessions"
CONVERSATION_TURNS_TAB = "conversation_turns"
CONVERSATION_CUSTOMER_CLAIMS_TAB = "conversation_customer_claims"
DEFERRED_INBOUND_TAB = "deferred_inbound"

ORDER_STATUSES = (
    "draft",
    "confirmed",
    "in_preparation",
    "ready",
    "delivered",
    "picked_up",
    "cancelled",
)


STOCK_REASONS = (
    "sale",
    "restock",
    "adjustment",
    "reversal",
)

TABS = {
    PRODUCTS_TAB: [
        "product_id",
        "tenant_id",
        "product_name",
        "aliases",
        "category",
        "available_days",
        "unit",
        "unit_price",
        "active",
        "current_stock",
        "min_stock",
        "notes",
        "created_at",
        "updated_at",
    ],
    CUSTOMERS_TAB: [
        "customer_id",
        "tenant_id",
        "customer_name",
        "customer_phone",
        "default_address",
        "notes",
        "created_at",
        "updated_at",
        "last_order_at",
    ],
    ORDERS_TAB: [
        "order_id",
        "tenant_id",
        "created_at",
        "updated_at",
        "customer_id",
        "customer_name_snapshot",
        "customer_phone_snapshot",
        "conversation_id",
        "raw_message",
        "status",
        "confirmed_at",
        "status_updated_at",
        "subtotal",
        "delivery_fee",
        "packaging_fee",
        "total",
        "fulfillment_type",
        "delivery_zone",
        "customer_notes",
        "payment_method",
        "delivery_date",
        "delivery_address",
        "notes",
        "confirmation_message",
        "created_by",
    ],
    ORDER_ITEMS_TAB: [
        "order_item_id",
        "tenant_id",
        "order_id",
        "product_id",
        "product_name_snapshot",
        "unit_snapshot",
        "quantity",
        "unit_price_snapshot",
        "line_total",
        "modifications",
        "validation_status",
        "notes",
    ],
    STOCK_MOVEMENTS_TAB: [
        "stock_movement_id",
        "tenant_id",
        "created_at",
        "product_id",
        "quantity_delta",
        "reason",
        "reference_id",
        "notes",
        "created_by",
    ],
    PARSE_LOG_TAB: [
        "parse_id",
        "tenant_id",
        "created_at",
        "raw_message",
        "parsed_json",
        "model",
        "prompt_version",
        "latency_ms",
        "success",
        "error",
    ],
}
