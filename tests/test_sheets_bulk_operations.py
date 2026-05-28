from datetime import datetime, timezone
from decimal import Decimal

import pytest

from duna_orders.domain.models import Customer, OrderItem
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.test_storage_contract import make_order


pytestmark = pytest.mark.live_sheets


def test_bulk_create_orders_round_trips_small_batch(
    live_sheets_storage,
    live_sheets_run_tokens,
):
    token = "bulk_orders_"
    live_sheets_run_tokens.append(token)

    orders = [
        make_order(
            token,
            order_id=f"{token}ord_{index}",
            created_at=datetime(2026, 5, 27, 12, index, tzinfo=timezone.utc),
            customer_id=f"{token}cus_{index}",
        )
        for index in range(2)
    ]

    live_sheets_storage.bulk_create_orders(orders)

    saved_order = live_sheets_storage.get_order(f"{token}ord_0")

    assert saved_order is not None
    assert saved_order.order_id == f"{token}ord_0"
    assert saved_order.customer_id == f"{token}cus_0"


def test_bulk_create_order_items_round_trips_small_batch(
    live_sheets_storage,
    live_sheets_run_tokens,
):
    token = "bulk_items_"
    live_sheets_run_tokens.append(token)

    order = make_order(
        token,
        order_id=f"{token}ord_parent",
        created_at=datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc),
        customer_id=f"{token}cus_parent",
    ).model_copy(update={"items": []}, deep=True)

    items = [
        OrderItem(
            tenant_id=DEFAULT_TEST_TENANT_ID,
            order_item_id=f"{token}oit_{index}",
            order_id=order.order_id,
            product_id=f"{token}prd_{index}",
            product_name_snapshot=f"Producto {index}",
            unit_snapshot="unidad",
            quantity=Decimal("1"),
            unit_price_snapshot=Decimal("1000"),
            line_total=Decimal("1000"),
            modifications=None,
            validation_status="ok",
            notes=None,
        )
        for index in range(2)
    ]

    live_sheets_storage.bulk_create_orders([order])
    live_sheets_storage.bulk_create_order_items(items)

    saved_order = live_sheets_storage.get_order(order.order_id)

    assert saved_order is not None
    assert [item.order_item_id for item in saved_order.items] == [
        f"{token}oit_0",
        f"{token}oit_1",
    ]


def test_bulk_delete_orders_by_id_prefix_deletes_orders_and_items(
    live_sheets_storage,
    live_sheets_run_tokens,
):
    token = "bulk_delete_"
    live_sheets_run_tokens.append(token)

    order = make_order(
        token,
        order_id=f"{token}ord_delete",
        created_at=datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc),
        customer_id=f"{token}cus_delete",
    )

    live_sheets_storage.bulk_create_order_items(order.items)
    live_sheets_storage.bulk_create_orders([order])

    deleted_rows = live_sheets_storage.bulk_delete_orders_by_id_prefix(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        prefix=f"{token}ord_",
    )

    saved_order = live_sheets_storage.get_order(order.order_id)

    assert deleted_rows == 2
    assert saved_order is None

def test_bulk_create_customers_round_trips_small_batch(
    live_sheets_storage,
    live_sheets_run_tokens,
):
    token = "bulk_customers_"
    live_sheets_run_tokens.append(token)
    created_at = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)

    customers = [
        Customer(
            tenant_id=DEFAULT_TEST_TENANT_ID,
            customer_id=f"{token}cus_{index}",
            customer_name=f"Cliente Bulk {index}",
            customer_phone=f"30000000{index}",
            default_address=None,
            notes=None,
            created_at=created_at,
            updated_at=created_at,
            last_order_at=None,
        )
        for index in range(2)
    ]

    live_sheets_storage.bulk_create_customers(customers)

    saved_customer = live_sheets_storage.get_customer(f"{token}cus_0")

    assert saved_customer is not None
    assert saved_customer.customer_id == f"{token}cus_0"
    assert saved_customer.customer_name == "Cliente Bulk 0"