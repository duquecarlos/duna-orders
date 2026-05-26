from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from duna_orders.domain.models import OrderItem
from duna_orders.services.dashboard_read_scenario import (
    LOCKED_DASHBOARD_READ_BUDGET,
    LOCKED_DASHBOARD_TAB_UNION,
    LOCKED_DASHBOARD_WIDGETS,
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


def _extra_item(
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


def _storage_for_locked_scenario():
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
                _extra_item(
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


def test_locked_dashboard_scenario_definition_uses_four_tabs():
    widget_names = {widget["name"] for widget in LOCKED_DASHBOARD_WIDGETS}

    assert widget_names == {
        "today_pulse",
        "week_trend",
        "status_breakdown",
        "time_of_day_heatmap",
        "customer_mix",
        "top_customers",
        "top_items_this_week",
        "item_pairs",
    }
    assert LOCKED_DASHBOARD_TAB_UNION == {
        ORDERS_TAB,
        ORDER_ITEMS_TAB,
        CUSTOMERS_TAB,
        PRODUCTS_TAB,
    }


def test_locked_dashboard_cold_cache_page_render_stays_within_read_budget():
    storage = _storage_for_locked_scenario()

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

    assert result.today_pulse["orders_count"] == 1
    assert result.item_pairs
    assert total_reads <= LOCKED_DASHBOARD_READ_BUDGET

    for tab_name in LOCKED_DASHBOARD_TAB_UNION:
        assert per_tab_reads[tab_name] <= 1

    for tab_name in set(TABS) - LOCKED_DASHBOARD_TAB_UNION:
        assert per_tab_reads[tab_name] == 0