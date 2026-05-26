from __future__ import annotations

import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from duna_orders.domain.models import OrderItem
from duna_orders.services.dashboard_read_scenario import (
    LOCKED_DASHBOARD_READ_BUDGET,
    LOCKED_DASHBOARD_TAB_UNION,
    run_locked_dashboard_read_scenario,
)
from duna_orders.storage.read_context import sheets_request_context
from duna_orders.storage.schema import (
    CUSTOMERS_TAB,
    ORDER_ITEMS_TAB,
    ORDERS_TAB,
    PRODUCTS_TAB,
    TABS,
)
from tests._fakes import make_fake_google_sheets_storage
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.test_storage_contract import make_customer, make_order, make_product


NOW = datetime(2026, 5, 26, 15, 0, tzinfo=timezone.utc)
TIMEZONE_NAME = "America/Bogota"


def _record(tab_name: str, row: list[object]) -> dict[str, object]:
    return dict(zip(TABS[tab_name], row))


def _second_item_from_order(
    *,
    order_id: str,
    product_id: str,
    product_name: str,
    quantity: Decimal = Decimal("1"),
    unit_price: Decimal = Decimal("5000"),
) -> OrderItem:
    return OrderItem(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        order_item_id=f"{order_id}_{product_id}_extra",
        order_id=order_id,
        product_id=product_id,
        product_name_snapshot=product_name,
        unit_snapshot="unidad",
        quantity=quantity,
        unit_price_snapshot=unit_price,
        line_total=quantity * unit_price,
        modifications=None,
        validation_status="ok",
    )


def _fake_storage_for_locked_scenario():
    storage = make_fake_google_sheets_storage()

    products = [
        make_product(
            "budget_",
            product_id="budget_prd_arepa",
            product_name="Arepa",
            unit_price=Decimal("8000"),
        ),
        make_product(
            "budget_",
            product_id="budget_prd_jugo",
            product_name="Jugo",
            unit_price=Decimal("5000"),
        ),
        make_product(
            "budget_",
            product_id="budget_prd_postre",
            product_name="Postre",
            unit_price=Decimal("7000"),
        ),
    ]

    customers = [
        make_customer("budget_", customer_id="budget_cus_ana", phone="3001111111"),
        make_customer("budget_", customer_id="budget_cus_luis", phone="3002222222"),
    ]

    order_today = make_order(
        "budget_",
        order_id="budget_ord_today",
        product_id="budget_prd_arepa",
        status="confirmed",
        created_at=NOW,
        customer_id="budget_cus_ana",
    )
    order_today = order_today.model_copy(
        update={
            "items": [
                order_today.items[0],
                _second_item_from_order(
                    order_id=order_today.order_id,
                    product_id="budget_prd_jugo",
                    product_name="Jugo",
                ),
            ],
            "subtotal": Decimal("13000"),
            "total": Decimal("13000"),
        },
        deep=True,
    )

    order_week = make_order(
        "budget_",
        order_id="budget_ord_week",
        product_id="budget_prd_postre",
        status="delivered",
        created_at=datetime(2026, 5, 24, 17, 0, tzinfo=timezone.utc),
        customer_id="budget_cus_luis",
    )
    order_week = order_week.model_copy(
        update={
            "subtotal": Decimal("7000"),
            "total": Decimal("7000"),
        },
        deep=True,
    )

    order_old = make_order(
        "budget_",
        order_id="budget_ord_old",
        product_id="budget_prd_arepa",
        status="cancelled",
        created_at=datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc),
        customer_id="budget_cus_luis",
    )

    orders = [order_today, order_week, order_old]

    storage._spreadsheet.set_records_by_tab(
        {
            PRODUCTS_TAB: [
                _record(PRODUCTS_TAB, storage._product_to_row(product))
                for product in products
            ],
            CUSTOMERS_TAB: [
                _record(CUSTOMERS_TAB, storage._customer_to_row(customer))
                for customer in customers
            ],
            ORDERS_TAB: [
                _record(ORDERS_TAB, storage._order_to_row(order))
                for order in orders
            ],
            ORDER_ITEMS_TAB: [
                _record(ORDER_ITEMS_TAB, storage._order_item_to_row(item))
                for order in orders
                for item in order.items
            ],
        }
    )

    return storage


def main() -> int:
    storage = _fake_storage_for_locked_scenario()

    with sheets_request_context(storage):
        result = run_locked_dashboard_read_scenario(
            storage,
            tenant_id=DEFAULT_TEST_TENANT_ID,
            now=NOW,
            timezone_name=TIMEZONE_NAME,
        )

    per_tab_reads = {
        tab_name: storage._spreadsheet.read_count(tab_name)
        for tab_name in TABS
    }
    total_reads = sum(per_tab_reads.values())
    extra_tabs = {
        tab_name: count
        for tab_name, count in per_tab_reads.items()
        if tab_name not in LOCKED_DASHBOARD_TAB_UNION and count > 0
    }
    overloaded_tabs = {
        tab_name: count
        for tab_name, count in per_tab_reads.items()
        if tab_name in LOCKED_DASHBOARD_TAB_UNION and count > 1
    }
    passed = (
        total_reads <= LOCKED_DASHBOARD_READ_BUDGET
        and not extra_tabs
        and not overloaded_tabs
    )

    print("Locked dashboard read-budget measurement")
    print(f"Total full-sheet reads: {total_reads}")
    print(f"Budget: <= {LOCKED_DASHBOARD_READ_BUDGET}")
    print("Per-tab reads:")
    for tab_name, count in per_tab_reads.items():
        print(f"- {tab_name}: {count}")
    print(f"Pass: {passed}")

    # Touch result to make the script fail loudly if scenario computation breaks.
    print(
        "Records loaded: "
        f"orders={len(result.orders)}, "
        f"order_items={len(result.order_items)}, "
        f"customers={len(result.customers)}, "
        f"products={len(result.products)}"
    )

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())