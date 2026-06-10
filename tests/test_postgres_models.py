from importlib import import_module

from sqlalchemy import JSON, Boolean, DateTime, Integer, Numeric, String, Text

from duna_orders.storage.postgres_base import Base
from duna_orders.storage.schema import (
    CUSTOMERS_TAB,
    ORDERS_TAB,
    ORDER_ITEMS_TAB,
    PARSE_LOG_TAB,
    PRODUCTS_TAB,
    STOCK_MOVEMENTS_TAB,
    TABS,
    OUTBOUND_MESSAGES_TAB,
    PROCESSED_MESSAGES_TAB,
    CONVERSATION_SESSIONS_TAB,
    CONVERSATION_TURNS_TAB,
)

POSTGRES_ONLY_TABLES = {
    "processed_messages",
    "order_status_transitions",
    "outbound_messages",
    "conversation_sessions",
    "conversation_turns",
}
PRIMARY_ID_COLUMNS = {
    PRODUCTS_TAB: "product_id",
    CUSTOMERS_TAB: "customer_id",
    ORDERS_TAB: "order_id",
    ORDER_ITEMS_TAB: "order_item_id",
    STOCK_MOVEMENTS_TAB: "stock_movement_id",
    PARSE_LOG_TAB: "parse_id",
    PROCESSED_MESSAGES_TAB: "message_sid",
    OUTBOUND_MESSAGES_TAB: "outbound_message_id",
    CONVERSATION_SESSIONS_TAB: "conversation_id",
    CONVERSATION_TURNS_TAB: "turn_id",
}


def load_postgres_models() -> None:
    import_module("duna_orders.storage.postgres_models")


def test_postgres_models_register_current_runtime_tables() -> None:
    load_postgres_models()

    assert set(Base.metadata.tables) == set(TABS) | POSTGRES_ONLY_TABLES


def test_postgres_model_columns_match_current_storage_schema() -> None:
    load_postgres_models()

    for table_name, expected_columns in TABS.items():
        table = Base.metadata.tables[table_name]

        assert list(table.columns.keys()) == expected_columns


def test_postgres_model_primary_keys_match_current_runtime_ids() -> None:
    load_postgres_models()

    for table_name, primary_id_column in PRIMARY_ID_COLUMNS.items():
        table = Base.metadata.tables[table_name]

        assert [column.name for column in table.primary_key.columns] == [primary_id_column]

def test_processed_messages_table_is_postgres_only() -> None:
    load_postgres_models()

    table = Base.metadata.tables[PROCESSED_MESSAGES_TAB]

    assert list(table.columns.keys()) == [
        "message_sid",
        "tenant_id",
        "received_at",
        "from_number",
        "raw_body",
        "resulting_order_id",
    ]
    assert PROCESSED_MESSAGES_TAB not in TABS
def test_order_items_reference_orders_with_cascade_delete() -> None:
    load_postgres_models()

    order_items = Base.metadata.tables[ORDER_ITEMS_TAB]
    orders = Base.metadata.tables[ORDERS_TAB]
    foreign_keys = order_items.c.order_id.foreign_keys

    assert len(foreign_keys) == 1

    foreign_key = next(iter(foreign_keys))

    assert foreign_key.column is orders.c.order_id
    assert foreign_key.ondelete == "CASCADE"


def test_json_columns_cover_list_like_domain_fields() -> None:
    load_postgres_models()

    products = Base.metadata.tables[PRODUCTS_TAB]

    assert isinstance(products.c.aliases.type, JSON)
    assert isinstance(products.c.available_days.type, JSON)


def test_decimal_columns_use_numeric_types() -> None:
    load_postgres_models()

    decimal_columns = [
        Base.metadata.tables[PRODUCTS_TAB].c.unit_price,
        Base.metadata.tables[PRODUCTS_TAB].c.current_stock,
        Base.metadata.tables[PRODUCTS_TAB].c.min_stock,
        Base.metadata.tables[ORDERS_TAB].c.subtotal,
        Base.metadata.tables[ORDERS_TAB].c.delivery_fee,
        Base.metadata.tables[ORDERS_TAB].c.packaging_fee,
        Base.metadata.tables[ORDERS_TAB].c.total,
        Base.metadata.tables[ORDER_ITEMS_TAB].c.quantity,
        Base.metadata.tables[ORDER_ITEMS_TAB].c.unit_price_snapshot,
        Base.metadata.tables[ORDER_ITEMS_TAB].c.line_total,
        Base.metadata.tables[STOCK_MOVEMENTS_TAB].c.quantity_delta,
    ]

    assert all(isinstance(column.type, Numeric) for column in decimal_columns)


def test_datetime_boolean_integer_and_text_columns_use_expected_types() -> None:
    load_postgres_models()

    assert isinstance(Base.metadata.tables[ORDERS_TAB].c.created_at.type, DateTime)
    assert isinstance(Base.metadata.tables[PRODUCTS_TAB].c.active.type, Boolean)
    assert isinstance(Base.metadata.tables[PARSE_LOG_TAB].c.success.type, Boolean)
    assert isinstance(Base.metadata.tables[PARSE_LOG_TAB].c.latency_ms.type, Integer)
    assert isinstance(Base.metadata.tables[ORDERS_TAB].c.raw_message.type, Text)
    assert isinstance(Base.metadata.tables[PARSE_LOG_TAB].c.parsed_json.type, Text)


def test_tenant_lookup_indexes_exist_for_expected_access_patterns() -> None:
    load_postgres_models()

    expected_indexes = {
        PRODUCTS_TAB: {
            "ix_products_tenant_id_active",
            "ix_products_tenant_id_category",
        },
        CUSTOMERS_TAB: {
            "ix_customers_tenant_id_phone",
        },
        ORDERS_TAB: {
            "ix_orders_tenant_id_status",
            "ix_orders_tenant_id_created_at",
            "ix_orders_tenant_id_customer_id",
        },
        ORDER_ITEMS_TAB: {
            "ix_order_items_tenant_id_order_id",
            "ix_order_items_tenant_id_product_id",
        },
        STOCK_MOVEMENTS_TAB: {
            "ix_stock_movements_tenant_id_product_id",
            "ix_stock_movements_tenant_id_created_at",
            "ix_stock_movements_tenant_id_reference_id",
        },
        PARSE_LOG_TAB: {
            "ix_parse_log_tenant_id_created_at",
            "ix_parse_log_tenant_id_success",
        },
        PROCESSED_MESSAGES_TAB: {
            "ix_processed_messages_tenant_id_received_at",
            "ix_processed_messages_tenant_id_resulting_order_id",
        },
    }

    for table_name, expected_table_indexes in expected_indexes.items():
        actual_indexes = {
            index.name
            for index in Base.metadata.tables[table_name].indexes
        }

        assert expected_table_indexes <= actual_indexes

def test_order_status_transitions_table_is_postgres_only() -> None:
    load_postgres_models()

    table = Base.metadata.tables["order_status_transitions"]

    assert [column.name for column in table.columns] == [
        "transition_id",
        "tenant_id",
        "order_id",
        "from_status",
        "to_status",
        "occurred_at",
        "source",
    ]
    assert table.c.transition_id.primary_key is True
    assert table.c.tenant_id.nullable is False
    assert table.c.order_id.nullable is False
    assert table.c.from_status.nullable is True
    assert table.c.to_status.nullable is False
    assert table.c.occurred_at.nullable is False
    assert table.c.source.nullable is False


def test_outbound_messages_table_is_postgres_only() -> None:
    load_postgres_models()

    table = Base.metadata.tables[OUTBOUND_MESSAGES_TAB]

    assert [column.name for column in table.columns] == [
        "outbound_message_id",
        "tenant_id",
        "order_id",
        "acknowledgement_type",
        "to_number",
        "from_number",
        "body",
        "status",
        "provider",
        "provider_message_id",
        "attempt_count",
        "last_error_code",
        "last_error_message",
        "requested_by",
        "created_at",
        "updated_at",
        "sent_at",
    ]
    assert OUTBOUND_MESSAGES_TAB not in TABS
    assert table.c.outbound_message_id.primary_key is True
    assert table.c.tenant_id.nullable is False
    assert table.c.order_id.nullable is False
    assert table.c.acknowledgement_type.nullable is False
    assert isinstance(table.c.body.type, Text)
    assert isinstance(table.c.status.type, String)
    assert isinstance(table.c.attempt_count.type, Integer)
    assert isinstance(table.c.created_at.type, DateTime)
    assert isinstance(table.c.updated_at.type, DateTime)


def test_outbound_messages_unique_constraint_and_indexes_exist() -> None:
    load_postgres_models()

    table = Base.metadata.tables[OUTBOUND_MESSAGES_TAB]
    unique_constraints = {
        constraint.name: [column.name for column in constraint.columns]
        for constraint in table.constraints
        if constraint.name
    }
    actual_indexes = {index.name for index in table.indexes}

    assert unique_constraints["uq_outbound_messages_tenant_order_ack_type"] == [
        "tenant_id",
        "order_id",
        "acknowledgement_type",
    ]
    assert {
        "ix_outbound_messages_tenant_id_order_id",
        "ix_outbound_messages_tenant_id_status",
        "ix_outbound_messages_tenant_id_created_at",
    } <= actual_indexes


def test_conversation_sessions_table_is_postgres_only() -> None:
    load_postgres_models()

    table = Base.metadata.tables[CONVERSATION_SESSIONS_TAB]

    assert [column.name for column in table.columns] == [
        "conversation_id",
        "tenant_id",
        "customer_phone",
        "status",
        "opened_at",
        "last_message_at",
        "version",
        "created_at",
        "updated_at",
    ]
    assert CONVERSATION_SESSIONS_TAB not in TABS
    assert table.c.conversation_id.primary_key is True
    assert table.c.tenant_id.nullable is False
    assert table.c.customer_phone.nullable is False
    assert table.c.status.nullable is False
    assert isinstance(table.c.version.type, Integer)
    assert isinstance(table.c.opened_at.type, DateTime)
    assert isinstance(table.c.last_message_at.type, DateTime)
    assert "resulting_order_id" not in table.c
    assert "latest_parse_status" not in table.c
    assert "latest_parse_error" not in table.c
    assert "accumulated_text" not in table.c


def test_conversation_turns_table_is_postgres_only() -> None:
    load_postgres_models()

    table = Base.metadata.tables[CONVERSATION_TURNS_TAB]

    assert [column.name for column in table.columns] == [
        "turn_id",
        "conversation_id",
        "tenant_id",
        "message_sid",
        "from_number",
        "body",
        "received_at",
        "sequence_number",
        "created_at",
    ]
    assert CONVERSATION_TURNS_TAB not in TABS
    assert table.c.turn_id.primary_key is True
    assert table.c.tenant_id.nullable is False
    assert table.c.message_sid.nullable is False
    assert isinstance(table.c.body.type, Text)
    assert isinstance(table.c.sequence_number.type, Integer)


def test_conversation_state_constraints_and_indexes_exist() -> None:
    load_postgres_models()

    sessions = Base.metadata.tables[CONVERSATION_SESSIONS_TAB]
    turns = Base.metadata.tables[CONVERSATION_TURNS_TAB]
    turn_unique_constraints = {
        constraint.name: [column.name for column in constraint.columns]
        for constraint in turns.constraints
        if constraint.name
    }
    session_indexes = {index.name for index in sessions.indexes}
    turn_indexes = {index.name for index in turns.indexes}

    assert "uq_conversation_sessions_one_open_per_customer" in session_indexes
    assert {
        "ix_conversation_sessions_tenant_id_customer_phone",
        "ix_conversation_sessions_tenant_id_status",
    } <= session_indexes
    assert turn_unique_constraints["uq_conversation_turns_tenant_message_sid"] == [
        "tenant_id",
        "message_sid",
    ]
    assert {
        "ix_conversation_turns_tenant_id_conversation_id",
        "ix_conversation_turns_conversation_sequence",
    } <= turn_indexes
