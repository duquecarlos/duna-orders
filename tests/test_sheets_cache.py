from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from duna_orders.storage.exceptions import StorageBackendError
from duna_orders.storage.read_context import sheets_request_context
from duna_orders.storage.schema import (
    CUSTOMERS_TAB,
    ORDER_ITEMS_TAB,
    ORDERS_TAB,
    PRODUCTS_TAB,
    STOCK_MOVEMENTS_TAB,
    TABS,
)
from tests._fakes import make_fake_google_sheets_storage
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.test_storage_contract import (
    make_customer,
    make_order,
    make_product,
    make_stock_movement,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _record(tab_name: str, row: list[object]) -> dict[str, object]:
    return dict(zip(TABS[tab_name], row))


def _storage_with_order_records(*, spreadsheet_id: str = "fake-spreadsheet-id"):
    clock = FakeClock()
    storage = make_fake_google_sheets_storage(
        spreadsheet_id=spreadsheet_id,
        time_source=clock,
    )
    token = "cache_"
    customer_id = f"{token}cus_history"

    old_order = make_order(
        token,
        order_id=f"{token}ord_old",
        status="confirmed",
        created_at=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
        customer_id=customer_id,
    )
    newest_order = make_order(
        token,
        order_id=f"{token}ord_newest",
        status="confirmed",
        created_at=datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc),
        customer_id=customer_id,
    )

    storage._spreadsheet.set_records_by_tab(
        {
            ORDERS_TAB: [
                _record(ORDERS_TAB, storage._order_to_row(old_order)),
                _record(ORDERS_TAB, storage._order_to_row(newest_order)),
            ],
            ORDER_ITEMS_TAB: [
                _record(ORDER_ITEMS_TAB, storage._order_item_to_row(old_order.items[0])),
                _record(
                    ORDER_ITEMS_TAB,
                    storage._order_item_to_row(newest_order.items[0]),
                ),
            ],
        }
    )

    return storage, clock, newest_order


def test_cache_hit_before_ttl_expiry_reuses_loaded_records():
    storage, _, _ = _storage_with_order_records()

    first_orders = storage.list_orders()
    second_orders = storage.list_orders()

    assert [order.order_id for order in first_orders] == [
        order.order_id for order in second_orders
    ]
    assert storage._spreadsheet.read_count(ORDERS_TAB) == 1
    assert storage._spreadsheet.read_count(ORDER_ITEMS_TAB) == 1


def test_cache_miss_after_ttl_expiry_reloads_records():
    storage, clock, _ = _storage_with_order_records()

    storage.list_orders()
    clock.advance(31)
    storage.list_orders()

    assert storage._spreadsheet.read_count(ORDERS_TAB) == 2
    assert storage._spreadsheet.read_count(ORDER_ITEMS_TAB) == 2


def test_failed_read_is_not_cached():
    storage, _, _ = _storage_with_order_records()
    storage._spreadsheet.fail_next_get_all_records(ORDERS_TAB, RuntimeError("boom"))

    with pytest.raises(StorageBackendError):
        storage._load_records(ORDERS_TAB)

    records = storage._load_records(ORDERS_TAB)

    assert records
    assert storage._spreadsheet.read_count(ORDERS_TAB) == 2


def test_cache_hits_return_safe_record_copies():
    storage, _, _ = _storage_with_order_records()

    first_records = storage._load_records(ORDERS_TAB)
    first_records[0]["status"] = "cancelled"

    second_records = storage._load_records(ORDERS_TAB)

    assert second_records[0]["status"] == "confirmed"
    assert storage._spreadsheet.read_count(ORDERS_TAB) == 1


def test_cache_is_isolated_per_spreadsheet_id():
    runtime_storage, _, runtime_order = _storage_with_order_records(
        spreadsheet_id="runtime-spreadsheet-id"
    )
    test_storage, _, test_order = _storage_with_order_records(
        spreadsheet_id="test-spreadsheet-id"
    )

    runtime_orders = runtime_storage.list_orders()
    test_orders = test_storage.list_orders()

    assert [order.order_id for order in runtime_orders] == [
        "cache_ord_old",
        runtime_order.order_id,
    ]
    assert [order.order_id for order in test_orders] == [
        "cache_ord_old",
        test_order.order_id,
    ]
    assert runtime_storage._spreadsheet.read_count(ORDERS_TAB) == 1
    assert test_storage._spreadsheet.read_count(ORDERS_TAB) == 1


def test_create_customer_invalidates_customers_cache():
    clock = FakeClock()
    storage = make_fake_google_sheets_storage(time_source=clock)
    customer = make_customer("cache_", customer_id="cache_cus_new")

    storage.list_customers()
    storage.create_customer(customer)
    customers = storage.list_customers()

    assert [saved.customer_id for saved in customers] == [customer.customer_id]
    assert storage._spreadsheet.read_count(CUSTOMERS_TAB) == 2


def test_create_order_invalidates_orders_and_order_items_cache():
    storage, _, _ = _storage_with_order_records()
    new_order = make_order(
        "cache_",
        order_id="cache_ord_created",
        customer_id="cache_cus_created",
    )

    storage.list_orders()
    storage.create_order(new_order)
    orders = storage.list_orders()

    assert "cache_ord_created" in [order.order_id for order in orders]
    assert storage._spreadsheet.read_count(ORDERS_TAB) == 2
    assert storage._spreadsheet.read_count(ORDER_ITEMS_TAB) == 2


def test_update_order_status_invalidates_orders_cache_only():
    storage, _, newest_order = _storage_with_order_records()
    changed_at = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)

    storage.list_orders()
    storage.update_order_status(
        newest_order.order_id,
        "delivered",
        status_updated_at=changed_at,
    )
    orders = storage.list_orders()

    updated_order = next(order for order in orders if order.order_id == newest_order.order_id)
    assert updated_order.status == "delivered"
    assert storage._spreadsheet.read_count(ORDERS_TAB) == 2
    assert storage._spreadsheet.read_count(ORDER_ITEMS_TAB) == 1


def test_append_stock_movement_invalidates_stock_movements_cache():
    clock = FakeClock()
    storage = make_fake_google_sheets_storage(time_source=clock)
    movement = make_stock_movement(
        "cache_",
        stock_movement_id="cache_mov_new",
    )

    storage.list_stock_movements()
    storage.append_stock_movement(movement)
    movements = storage.list_stock_movements()

    assert [saved.stock_movement_id for saved in movements] == [
        movement.stock_movement_id
    ]
    assert storage._spreadsheet.read_count(STOCK_MOVEMENTS_TAB) == 2


def test_upsert_product_invalidates_products_cache():
    clock = FakeClock()
    storage = make_fake_google_sheets_storage(time_source=clock)
    product = make_product(
        "cache_",
        product_id="cache_prd_new",
        current_stock=Decimal("12"),
    )

    storage.list_products(active_only=False)
    storage.upsert_product(product)
    products = storage.list_products(active_only=False)

    assert [saved.product_id for saved in products] == [product.product_id]
    assert storage._spreadsheet.read_count(PRODUCTS_TAB) == 2


def test_request_context_loaded_records_take_precedence_over_cache():
    storage, _, newest_order = _storage_with_order_records()

    with sheets_request_context(storage):
        first_orders = storage.list_orders()
        storage._records_cache.clear()
        storage._spreadsheet.set_records_by_tab(
            {
                ORDERS_TAB: [],
                ORDER_ITEMS_TAB: [],
            }
        )
        second_orders = storage.list_orders()

    assert [order.order_id for order in first_orders] == [
        "cache_ord_old",
        newest_order.order_id,
    ]
    assert [order.order_id for order in second_orders] == [
        "cache_ord_old",
        newest_order.order_id,
    ]
    assert storage._spreadsheet.read_count(ORDERS_TAB) == 1
    assert storage._spreadsheet.read_count(ORDER_ITEMS_TAB) == 1