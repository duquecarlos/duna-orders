from datetime import datetime, timezone

from duna_orders.storage.schema import (
    ORDER_ITEMS_TAB,
    ORDERS_TAB,
    STOCK_MOVEMENTS_TAB,
    TABS,
)
from tests._fakes import make_fake_google_sheets_storage
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.test_storage_contract import make_order, make_stock_movement


def _record(tab_name: str, row: list[object]) -> dict[str, object]:
    return dict(zip(TABS[tab_name], row))


def _storage_with_order_records():
    storage = make_fake_google_sheets_storage()
    token = "read_consolidation_"
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
    other_customer_order = make_order(
        token,
        order_id=f"{token}ord_other_customer",
        status="draft",
        created_at=datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc),
        customer_id=f"{token}cus_other",
    )
    movement = make_stock_movement(
        token,
        stock_movement_id=f"{token}mov_sale",
        product_id=f"{token}prd_test",
    )

    storage._spreadsheet.set_records_by_tab(
        {
            ORDERS_TAB: [
                _record(ORDERS_TAB, storage._order_to_row(old_order)),
                _record(ORDERS_TAB, storage._order_to_row(newest_order)),
                _record(ORDERS_TAB, storage._order_to_row(other_customer_order)),
            ],
            ORDER_ITEMS_TAB: [
                _record(ORDER_ITEMS_TAB, storage._order_item_to_row(old_order.items[0])),
                _record(ORDER_ITEMS_TAB, storage._order_item_to_row(newest_order.items[0])),
                _record(
                    ORDER_ITEMS_TAB,
                    storage._order_item_to_row(other_customer_order.items[0]),
                ),
            ],
            STOCK_MOVEMENTS_TAB: [
                _record(
                    STOCK_MOVEMENTS_TAB,
                    storage._stock_movement_to_row(movement),
                ),
            ],
        }
    )

    return storage, customer_id, newest_order, movement


def test_record_set_loads_each_needed_tab_at_most_once_within_operation():
    storage, _, _, _ = _storage_with_order_records()
    record_set = storage._new_record_set()

    first_orders = storage._orders_from_records(record_set)
    second_orders = storage._orders_from_records(record_set)

    assert [order.order_id for order in first_orders] == [
        order.order_id for order in second_orders
    ]
    assert storage._spreadsheet.read_count(ORDERS_TAB) == 1
    assert storage._spreadsheet.read_count(ORDER_ITEMS_TAB) == 1


def test_sheets_read_consolidation_preserves_order_read_behavior():
    storage, customer_id, newest_order, _ = _storage_with_order_records()

    found_order = storage.get_order(newest_order.order_id)
    confirmed_orders = storage.list_orders(status="confirmed")
    history = storage.get_customer_order_history(
        customer_id,
        DEFAULT_TEST_TENANT_ID,
        limit=1,
    )

    assert found_order is not None
    assert found_order.order_id == newest_order.order_id
    assert [item.order_id for item in found_order.items] == [newest_order.order_id]
    assert [order.order_id for order in confirmed_orders] == [
        "read_consolidation_ord_old",
        "read_consolidation_ord_newest",
    ]
    assert [order.order_id for order in history] == [newest_order.order_id]


def test_sheets_read_consolidation_preserves_stock_movement_behavior():
    storage, _, _, movement = _storage_with_order_records()

    movements = storage.list_stock_movements(product_id=movement.product_id)

    assert len(movements) == 1
    assert movements[0].stock_movement_id == movement.stock_movement_id
    assert movements[0].product_id == movement.product_id