from __future__ import annotations

from datetime import datetime

from duna_orders.services.dashboard import DashboardScenarioResult
from duna_orders.services.tenant_scoped_reads import TenantScopedReadService
from duna_orders.storage.base import StorageInterface
from duna_orders.storage.schema import (
    CUSTOMERS_TAB,
    ORDER_ITEMS_TAB,
    ORDERS_TAB,
    PRODUCTS_TAB,
)


LOCKED_DASHBOARD_READ_BUDGET = 4
LOCKED_DASHBOARD_TAB_UNION = frozenset(
    {
        ORDERS_TAB,
        ORDER_ITEMS_TAB,
        CUSTOMERS_TAB,
        PRODUCTS_TAB,
    }
)

LOCKED_DASHBOARD_WIDGETS = (
    {
        "name": "today_pulse",
        "description": "Orders count today, revenue today, AOV today.",
        "tabs": frozenset({ORDERS_TAB, ORDER_ITEMS_TAB}),
    },
    {
        "name": "week_trend",
        "description": "Orders count and revenue per day, last 7 days.",
        "tabs": frozenset({ORDERS_TAB, ORDER_ITEMS_TAB}),
    },
    {
        "name": "week_over_week",
        "description": "Current week-to-date KPIs compared with prior week-to-date.",
        "tabs": frozenset({ORDERS_TAB}),
    },
    {
        "name": "time_of_day_heatmap",
        "description": "Weekday by hour order-count heatmap.",
        "tabs": frozenset({ORDERS_TAB}),
    },
    {
        "name": "customer_mix",
        "description": "New vs repeat customers this week.",
        "tabs": frozenset({ORDERS_TAB, CUSTOMERS_TAB}),
    },
    {
        "name": "top_customers",
        "description": "Top customers by total spend.",
        "tabs": frozenset({CUSTOMERS_TAB, ORDERS_TAB}),
    },
    {
        "name": "top_items_by_category",
        "description": "Top products by quantity sold this week.",
        "tabs": frozenset({ORDER_ITEMS_TAB, PRODUCTS_TAB}),
    },
    {
        "name": "item_pairs",
        "description": "Top product pairs by order-level co-occurrence.",
        "tabs": frozenset({ORDER_ITEMS_TAB}),
    },
)


def run_locked_dashboard_read_scenario(
    storage: StorageInterface,
    *,
    tenant_id: str,
    now: datetime,
    timezone_name: str,
) -> DashboardScenarioResult:
    del now
    del timezone_name

    scoped_reads = TenantScopedReadService(storage)
    orders = scoped_reads.list_orders(tenant_id=tenant_id)
    order_items = [
        item
        for order in orders
        for item in order.items
        if item.tenant_id == tenant_id
    ]
    customers = scoped_reads.list_customers(tenant_id=tenant_id)
    products = scoped_reads.list_products(tenant_id=tenant_id, active_only=False)

    return DashboardScenarioResult(
        orders=orders,
        order_items=order_items,
        customers=customers,
        products=products,
    )
