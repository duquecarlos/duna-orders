from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import combinations
from zoneinfo import ZoneInfo

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
        "name": "status_breakdown",
        "description": "Counts by draft, confirmed, completed, cancelled.",
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
        "name": "top_items_this_week",
        "description": "Top products by quantity sold this week.",
        "tabs": frozenset({ORDER_ITEMS_TAB, PRODUCTS_TAB}),
    },
    {
        "name": "item_pairs",
        "description": "Top product pairs by order-level co-occurrence.",
        "tabs": frozenset({ORDER_ITEMS_TAB}),
    },
)


@dataclass(frozen=True)
class LockedDashboardScenarioResult:
    today_pulse: dict[str, object]
    week_trend: list[dict[str, object]]
    status_breakdown: dict[str, int]
    time_of_day_heatmap: list[dict[str, object]]
    customer_mix: dict[str, object]
    top_customers: list[dict[str, object]]
    top_items_this_week: list[dict[str, object]]
    item_pairs: list[dict[str, object]]


def _local_datetime(value: datetime, timezone_name: str) -> datetime:
    return value.astimezone(ZoneInfo(timezone_name))


def _status_bucket(status: str) -> str:
    if status == "draft":
        return "draft"

    if status == "cancelled":
        return "cancelled"

    if status in {"delivered", "picked_up"}:
        return "completed"

    return "confirmed"


def run_locked_dashboard_read_scenario(
    storage: StorageInterface,
    *,
    tenant_id: str,
    now: datetime,
    timezone_name: str,
) -> LockedDashboardScenarioResult:
    timezone = ZoneInfo(timezone_name)
    today = now.astimezone(timezone).date()
    week_start = today - timedelta(days=6)
    week_dates = [week_start + timedelta(days=offset) for offset in range(7)]

    orders = [
        order
        for order in storage.list_orders()
        if order.tenant_id == tenant_id
    ]
    customers_by_id = {
        customer.customer_id: customer
        for customer in storage.list_customers()
        if customer.tenant_id == tenant_id
    }
    products_by_id = {
        product.product_id: product
        for product in storage.list_products(active_only=False)
        if product.tenant_id == tenant_id
    }

    today_orders = [
        order
        for order in orders
        if _local_datetime(order.created_at, timezone_name).date() == today
    ]
    week_orders = [
        order
        for order in orders
        if week_start <= _local_datetime(order.created_at, timezone_name).date() <= today
    ]

    today_revenue = sum((order.total for order in today_orders), Decimal("0"))
    today_count = len(today_orders)

    today_pulse = {
        "orders_count": today_count,
        "revenue": today_revenue,
        "aov": today_revenue / today_count if today_count else Decimal("0"),
    }

    week_trend: list[dict[str, object]] = []
    for day in week_dates:
        day_orders = [
            order
            for order in week_orders
            if _local_datetime(order.created_at, timezone_name).date() == day
        ]
        week_trend.append(
            {
                "date": day.isoformat(),
                "orders_count": len(day_orders),
                "revenue": sum((order.total for order in day_orders), Decimal("0")),
            }
        )

    status_counter = Counter(_status_bucket(order.status) for order in orders)
    status_breakdown = {
        "draft": status_counter["draft"],
        "confirmed": status_counter["confirmed"],
        "completed": status_counter["completed"],
        "cancelled": status_counter["cancelled"],
    }

    heatmap_counter = Counter(
        (
            _local_datetime(order.created_at, timezone_name).weekday(),
            _local_datetime(order.created_at, timezone_name).hour,
        )
        for order in orders
    )
    time_of_day_heatmap = [
        {
            "weekday": weekday,
            "hour": hour,
            "orders_count": count,
        }
        for (weekday, hour), count in sorted(heatmap_counter.items())
    ]

    first_order_date_by_customer: dict[str, object] = {}
    for order in sorted(orders, key=lambda item: item.created_at):
        if order.customer_id is None:
            continue

        first_order_date_by_customer.setdefault(
            order.customer_id,
            _local_datetime(order.created_at, timezone_name).date(),
        )

    week_customer_ids = {
        order.customer_id
        for order in week_orders
        if order.customer_id is not None
    }
    new_customers = {
        customer_id
        for customer_id in week_customer_ids
        if first_order_date_by_customer.get(customer_id) is not None
        and first_order_date_by_customer[customer_id] >= week_start
    }
    repeat_customers = week_customer_ids - new_customers
    total_week_customers = len(week_customer_ids)

    customer_mix = {
        "new_customers": len(new_customers),
        "repeat_customers": len(repeat_customers),
        "new_pct": (
            Decimal(len(new_customers)) / Decimal(total_week_customers)
            if total_week_customers
            else Decimal("0")
        ),
        "repeat_pct": (
            Decimal(len(repeat_customers)) / Decimal(total_week_customers)
            if total_week_customers
            else Decimal("0")
        ),
    }

    spend_by_customer: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for order in orders:
        if order.customer_id is not None:
            spend_by_customer[order.customer_id] += order.total

    top_customers = [
        {
            "customer_id": customer_id,
            "customer_name": customers_by_id[customer_id].customer_name
            if customer_id in customers_by_id
            else "",
            "total_spend": total_spend,
        }
        for customer_id, total_spend in sorted(
            spend_by_customer.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:10]
    ]

    quantity_by_product: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    pair_counter: Counter[tuple[str, str]] = Counter()

    for order in week_orders:
        product_ids_in_order = sorted({item.product_id for item in order.items})

        for item in order.items:
            quantity_by_product[item.product_id] += item.quantity

        for product_a, product_b in combinations(product_ids_in_order, 2):
            pair_counter[(product_a, product_b)] += 1

    top_items_this_week = [
        {
            "product_id": product_id,
            "product_name": products_by_id[product_id].product_name
            if product_id in products_by_id
            else "",
            "quantity": quantity,
        }
        for product_id, quantity in sorted(
            quantity_by_product.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:5]
    ]

    item_pairs = [
        {
            "product_pair": pair,
            "count": count,
        }
        for pair, count in pair_counter.most_common(5)
    ]

    return LockedDashboardScenarioResult(
        today_pulse=today_pulse,
        week_trend=week_trend,
        status_breakdown=status_breakdown,
        time_of_day_heatmap=time_of_day_heatmap,
        customer_mix=customer_mix,
        top_customers=top_customers,
        top_items_this_week=top_items_this_week,
        item_pairs=item_pairs,
    )