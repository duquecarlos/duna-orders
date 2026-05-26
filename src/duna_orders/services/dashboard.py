from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from duna_orders.domain.models import Customer, Order, OrderItem, Product


DASHBOARD_TIMEZONE = "America/Bogota"


@dataclass(frozen=True)
class DashboardScenarioResult:
    orders: list[Order]
    order_items: list[OrderItem]
    customers: list[Customer]
    products: list[Product]


@dataclass(frozen=True)
class TodaysPulse:
    orders_count: int
    revenue: Decimal
    aov: Decimal


@dataclass(frozen=True)
class WeekTrendDay:
    date: date
    orders_count: int
    revenue: Decimal


@dataclass(frozen=True)
class StatusBreakdown:
    draft: int
    confirmed: int
    completed: int
    cancelled: int


@dataclass(frozen=True)
class CustomerMix:
    new_customers: int
    repeat_customers: int
    new_pct: Decimal
    repeat_pct: Decimal


def _local_datetime(value: datetime) -> datetime:
    return value.astimezone(ZoneInfo(DASHBOARD_TIMEZONE))


def _status_bucket(status: str) -> str:
    if status == "draft":
        return "draft"

    if status == "cancelled":
        return "cancelled"

    if status in {"delivered", "picked_up"}:
        return "completed"

    return "confirmed"


def compute_todays_pulse(
    scenario: DashboardScenarioResult,
    *,
    today: date,
) -> TodaysPulse:
    today_orders = [
        order
        for order in scenario.orders
        if _local_datetime(order.created_at).date() == today
    ]

    orders_count = len(today_orders)
    revenue = sum((order.total for order in today_orders), Decimal("0"))

    return TodaysPulse(
        orders_count=orders_count,
        revenue=revenue,
        aov=revenue / orders_count if orders_count else Decimal("0"),
    )


def compute_week_trend(
    scenario: DashboardScenarioResult,
    *,
    today: date,
) -> list[WeekTrendDay]:
    week_start = today - timedelta(days=6)
    days = [week_start + timedelta(days=offset) for offset in range(7)]

    orders_by_day: dict[date, list[Order]] = {day: [] for day in days}

    for order in scenario.orders:
        local_date = _local_datetime(order.created_at).date()
        if week_start <= local_date <= today:
            orders_by_day[local_date].append(order)

    return [
        WeekTrendDay(
            date=day,
            orders_count=len(orders_by_day[day]),
            revenue=sum((order.total for order in orders_by_day[day]), Decimal("0")),
        )
        for day in days
    ]


def compute_status_breakdown(
    scenario: DashboardScenarioResult,
) -> StatusBreakdown:
    status_counter = Counter(_status_bucket(order.status) for order in scenario.orders)

    return StatusBreakdown(
        draft=status_counter["draft"],
        confirmed=status_counter["confirmed"],
        completed=status_counter["completed"],
        cancelled=status_counter["cancelled"],
    )


def compute_customer_mix(
    scenario: DashboardScenarioResult,
    *,
    week_start: date,
) -> CustomerMix:
    week_end = week_start + timedelta(days=6)

    first_order_date_by_customer: dict[str, date] = {}
    for order in sorted(scenario.orders, key=lambda item: item.created_at):
        if order.customer_id is None:
            continue

        first_order_date_by_customer.setdefault(
            order.customer_id,
            _local_datetime(order.created_at).date(),
        )

    week_customer_ids = {
        order.customer_id
        for order in scenario.orders
        if order.customer_id is not None
        and week_start <= _local_datetime(order.created_at).date() <= week_end
    }

    new_customers = {
        customer_id
        for customer_id in week_customer_ids
        if first_order_date_by_customer[customer_id] >= week_start
    }
    repeat_customers = week_customer_ids - new_customers
    total_customers = len(week_customer_ids)

    return CustomerMix(
        new_customers=len(new_customers),
        repeat_customers=len(repeat_customers),
        new_pct=(
            Decimal(len(new_customers)) / Decimal(total_customers)
            if total_customers
            else Decimal("0")
        ),
        repeat_pct=(
            Decimal(len(repeat_customers)) / Decimal(total_customers)
            if total_customers
            else Decimal("0")
        ),
    )