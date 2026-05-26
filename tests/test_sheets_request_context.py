from __future__ import annotations

from datetime import datetime, timezone

import pytest

from duna_orders.storage.read_context import sheets_request_context
from duna_orders.storage.schema import ORDER_ITEMS_TAB, ORDERS_TAB, TABS
from tests._fakes import make_fake_google_sheets_storage
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.test_storage_contract import make_order


def _record(tab_name: str, row: list[object]) -> dict[str, object]:
    return dict(zip(TABS[tab_name], row))


def _storage_with_order_records():
    storage = make_fake_google_sheets_storage()
    token = "request_context_"
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

    return storage, customer_id, newest_order


def test_request_context_reuses_records_across_storage_methods():
    storage, customer_id, newest_order = _storage_with_order_records()

    with sheets_request_context(storage):
        found_order = storage.get_order(newest_order.order_id)
        confirmed_orders = storage.list_orders(status="confirmed")
        history = storage.get_customer_order_history(
            customer_id,
            DEFAULT_TEST_TENANT_ID,
            limit=1,
        )

    assert found_order is not None
    assert found_order.order_id == newest_order.order_id
    assert [order.order_id for order in confirmed_orders] == [
        "request_context_ord_old",
        "request_context_ord_newest",
    ]
    assert [order.order_id for order in history] == [newest_order.order_id]
    assert storage._spreadsheet.read_count(ORDERS_TAB) == 1
    assert storage._spreadsheet.read_count(ORDER_ITEMS_TAB) == 1


def test_reads_outside_request_context_can_reuse_cross_request_cache():
    storage, _, newest_order = _storage_with_order_records()

    storage.get_order(newest_order.order_id)
    storage.list_orders(status="confirmed")

    assert storage._spreadsheet.read_count(ORDERS_TAB) == 1
    assert storage._spreadsheet.read_count(ORDER_ITEMS_TAB) == 1


def test_request_context_returns_safe_model_copies():
    storage, _, newest_order = _storage_with_order_records()

    with sheets_request_context(storage):
        first_read = storage.get_order(newest_order.order_id)
        assert first_read is not None

        first_read.status = "cancelled"

        second_read = storage.get_order(newest_order.order_id)
        assert second_read is not None
        assert second_read.status == "confirmed"


def test_nested_request_contexts_are_rejected():
    storage, _, _ = _storage_with_order_records()

    with sheets_request_context(storage):
        with pytest.raises(RuntimeError, match="Nested sheets request contexts"):
            with sheets_request_context(storage):
                pass


def test_request_context_teardown_releases_request_scoped_record_set():
    storage, _, _ = _storage_with_order_records()

    with sheets_request_context(storage):
        storage.list_orders()

    assert storage._spreadsheet.read_count(ORDERS_TAB) == 1
    assert storage._spreadsheet.read_count(ORDER_ITEMS_TAB) == 1

    storage._records_cache.clear()

    with sheets_request_context(storage):
        storage.list_orders()

    assert storage._spreadsheet.read_count(ORDERS_TAB) == 2
    assert storage._spreadsheet.read_count(ORDER_ITEMS_TAB) == 2


def test_request_context_resets_after_exception():
    storage, _, _ = _storage_with_order_records()

    with pytest.raises(ValueError, match="boom"):
        with sheets_request_context(storage):
            storage.list_orders()
            raise ValueError("boom")

    storage._records_cache.clear()

    with sheets_request_context(storage):
        storage.list_orders()

    assert storage._spreadsheet.read_count(ORDERS_TAB) == 2
    assert storage._spreadsheet.read_count(ORDER_ITEMS_TAB) == 2