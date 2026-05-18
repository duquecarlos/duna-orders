PRODUCTS_TAB = "products"
CUSTOMERS_TAB = "customers"
ORDERS_TAB = "orders"
ORDER_ITEMS_TAB = "order_items"
STOCK_MOVEMENTS_TAB = "stock_movements"


ORDER_STATUSES = (
    "draft",
    "reviewed",
    "confirmed",
    "prepared",
    "delivered",
    "cancelled",
)


STOCK_REASONS = (
    "sale",
    "restock",
    "manual_adjustment",
    "correction",
    "cancelled_order_reversal",
    "reversal",
)


TABS = {
    PRODUCTS_TAB: [
        "product_id",
        "product_name",
        "aliases",
        "category",
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
        "created_at",
        "updated_at",
        "customer_id",
        "customer_name_snapshot",
        "customer_phone_snapshot",
        "raw_message",
        "status",
        "confirmed_at",
        "subtotal",
        "delivery_fee",
        "total",
        "delivery_date",
        "delivery_address",
        "notes",
        "confirmation_message",
        "created_by",
    ],
    ORDER_ITEMS_TAB: [
        "order_item_id",
        "order_id",
        "product_id",
        "product_name_snapshot",
        "unit_snapshot",
        "quantity",
        "unit_price_snapshot",
        "line_total",
        "validation_status",
        "notes",
    ],
    STOCK_MOVEMENTS_TAB: [
        "stock_movement_id",
        "created_at",
        "product_id",
        "quantity_delta",
        "reason",
        "related_order_id",
        "notes",
        "created_by",
    ],
}