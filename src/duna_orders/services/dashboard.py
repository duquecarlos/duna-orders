from __future__ import annotations

from collections import Counter
from itertools import combinations
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from duna_orders.domain.models import Customer, Order, OrderItem, Product


DASHBOARD_TIMEZONE = "America/Bogota"
DashboardMode = Literal["runtime", "demo"]
MetricFormat = Literal["count", "money", "percent"]
MetricDeltaUnit = Literal["absolute", "percentage_points"]
MetricArrow = Literal["up", "down", "flat", "none"]
MetricColor = Literal["green", "red", "neutral"]

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
class TodaysStatusStrip:
    completed: int
    pending: int
    cancelled: int
@dataclass(frozen=True)
class PeriodKpi:
    start_date: date
    end_date: date
    orders_count: int
    non_cancelled_orders_count: int
    cancelled_orders_count: int
    revenue: Decimal
    aov: Decimal
    cancellation_rate: Decimal


@dataclass(frozen=True)
class WeekOverWeekMetric:
    label: str
    value: Decimal
    previous_value: Decimal | None
    delta: Decimal | None
    value_format: MetricFormat
    delta_unit: MetricDeltaUnit
    higher_is_better: bool
    arrow: MetricArrow
    color: MetricColor


@dataclass(frozen=True)
class WeekOverWeekResult:
    current_period: PeriodKpi
    previous_period: PeriodKpi | None
    metrics: list[WeekOverWeekMetric]
    has_comparison: bool
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

@dataclass(frozen=True)
class TopCustomersEntry:
    customer_id: str
    customer_name: str
    order_count: int
    total_spend: Decimal


@dataclass(frozen=True)
class TopCustomersResult:
    entries: list[TopCustomersEntry]


@dataclass(frozen=True)
class TopItemsEntry:
    product_id: str
    product_name: str
    quantity: Decimal
    revenue: Decimal


@dataclass(frozen=True)
class TopItemsResult:
    entries: list[TopItemsEntry]
@dataclass(frozen=True)
class TopItemsCategoryEntry:
    category: str
    product_id: str
    product_name: str
    quantity: Decimal
    revenue: Decimal


@dataclass(frozen=True)
class TopItemsByCategoryResult:
    entries: list[TopItemsCategoryEntry]
    week_start: date
    week_end: date
@dataclass(frozen=True)
class TimeOfDayCell:
    weekday: int
    hour: int
    order_count: int


@dataclass(frozen=True)
class TimeOfDayHeatmapResult:
    cells: list[TimeOfDayCell]
    window_start: date
    window_end: date


@dataclass(frozen=True)
class ProductPairEntry:
    product_id_a: str
    product_name_a: str
    product_id_b: str
    product_name_b: str
    count: int


@dataclass(frozen=True)
class ProductPairsResult:
    pairs: list[ProductPairEntry]
    week_start: date
    limit: int

def _local_datetime(value: datetime) -> datetime:
    return value.astimezone(ZoneInfo(DASHBOARD_TIMEZONE))
def resolve_reference_date(
    orders: list[Order],
    mode: DashboardMode,
    *,
    today: date | None = None,
) -> date:
    """Resolve the dashboard reference date.

    Runtime mode uses the real current date. Demo mode uses the latest local
    order date so seeded demos stay evergreen without reseeding.
    """

    runtime_today = today or date.today()

    if mode != "demo":
        return runtime_today

    order_dates = [
        _local_datetime(order.created_at).date()
        for order in orders
    ]

    if not order_dates:
        return runtime_today

    return max(order_dates)

def _status_bucket(status: str) -> str:
    if status == "draft":
        return "draft"

    if status == "cancelled":
        return "cancelled"

    if status in {"delivered", "picked_up"}:
        return "completed"

    return "confirmed"
def _period_orders(
    orders: list[Order],
    *,
    start_date: date,
    end_date: date,
) -> list[Order]:
    return [
        order
        for order in orders
        if start_date <= _local_datetime(order.created_at).date() <= end_date
    ]


def _period_kpi(
    orders: list[Order],
    *,
    start_date: date,
    end_date: date,
) -> PeriodKpi:
    period_orders = _period_orders(
        orders,
        start_date=start_date,
        end_date=end_date,
    )
    cancelled_orders = [
        order for order in period_orders if _status_bucket(order.status) == "cancelled"
    ]
    non_cancelled_orders = [
        order for order in period_orders if _status_bucket(order.status) != "cancelled"
    ]

    orders_count = len(period_orders)
    non_cancelled_orders_count = len(non_cancelled_orders)
    cancelled_orders_count = len(cancelled_orders)
    revenue = sum((order.total for order in non_cancelled_orders), Decimal("0"))

    return PeriodKpi(
        start_date=start_date,
        end_date=end_date,
        orders_count=orders_count,
        non_cancelled_orders_count=non_cancelled_orders_count,
        cancelled_orders_count=cancelled_orders_count,
        revenue=revenue,
        aov=(
            revenue / Decimal(non_cancelled_orders_count)
            if non_cancelled_orders_count
            else Decimal("0")
        ),
        cancellation_rate=(
            Decimal(cancelled_orders_count) / Decimal(orders_count)
            if orders_count
            else Decimal("0")
        ),
    )


def _metric_arrow(delta: Decimal | None) -> MetricArrow:
    if delta is None:
        return "none"

    if delta > 0:
        return "up"

    if delta < 0:
        return "down"

    return "flat"


def _metric_color(
    *,
    delta: Decimal | None,
    higher_is_better: bool,
) -> MetricColor:
    if delta is None or delta == 0:
        return "neutral"

    improved = delta > 0 if higher_is_better else delta < 0
    return "green" if improved else "red"


def _week_over_week_metric(
    *,
    label: str,
    value: Decimal,
    previous_value: Decimal | None,
    value_format: MetricFormat,
    delta_unit: MetricDeltaUnit = "absolute",
    higher_is_better: bool,
) -> WeekOverWeekMetric:
    delta = value - previous_value if previous_value is not None else None

    return WeekOverWeekMetric(
        label=label,
        value=value,
        previous_value=previous_value,
        delta=delta,
        value_format=value_format,
        delta_unit=delta_unit,
        higher_is_better=higher_is_better,
        arrow=_metric_arrow(delta),
        color=_metric_color(
            delta=delta,
            higher_is_better=higher_is_better,
        ),
    )

def compute_todays_pulse(
    scenario: DashboardScenarioResult,
    *,
    today: date,
) -> TodaysPulse:
    kpi = _period_kpi(
        scenario.orders,
        start_date=today,
        end_date=today,
    )

    return TodaysPulse(
        orders_count=kpi.orders_count,
        revenue=kpi.revenue,
        aov=kpi.aov,
    )
def compute_todays_status_strip(
    scenario: DashboardScenarioResult,
    *,
    today: date,
) -> TodaysStatusStrip:
    today_orders = [
        order
        for order in scenario.orders
        if _local_datetime(order.created_at).date() == today
    ]
    status_counter = Counter(_status_bucket(order.status) for order in today_orders)

    return TodaysStatusStrip(
        completed=status_counter["completed"],
        pending=status_counter["draft"] + status_counter["confirmed"],
        cancelled=status_counter["cancelled"],
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
            revenue=_period_kpi(
                orders_by_day[day],
                start_date=day,
                end_date=day,
            ).revenue,
        )
        for day in days
    ]

def compute_week_over_week(
    scenario: DashboardScenarioResult,
    *,
    reference_date: date,
) -> WeekOverWeekResult:
    current_start = reference_date - timedelta(days=reference_date.weekday())
    current_end = reference_date
    previous_start = current_start - timedelta(days=7)
    previous_end = current_end - timedelta(days=7)

    current_period = _period_kpi(
        scenario.orders,
        start_date=current_start,
        end_date=current_end,
    )
    previous_period_candidate = _period_kpi(
        scenario.orders,
        start_date=previous_start,
        end_date=previous_end,
    )

    has_comparison = previous_period_candidate.orders_count > 0
    previous_period = previous_period_candidate if has_comparison else None

    previous_orders = (
        Decimal(previous_period.orders_count)
        if previous_period is not None
        else None
    )
    previous_revenue = previous_period.revenue if previous_period is not None else None
    previous_aov = previous_period.aov if previous_period is not None else None
    previous_cancellation_rate = (
        previous_period.cancellation_rate
        if previous_period is not None
        else None
    )

    return WeekOverWeekResult(
        current_period=current_period,
        previous_period=previous_period,
        has_comparison=has_comparison,
        metrics=[
            _week_over_week_metric(
                label="Orders",
                value=Decimal(current_period.orders_count),
                previous_value=previous_orders,
                value_format="count",
                higher_is_better=True,
            ),
            _week_over_week_metric(
                label="Revenue",
                value=current_period.revenue,
                previous_value=previous_revenue,
                value_format="money",
                higher_is_better=True,
            ),
            _week_over_week_metric(
                label="AOV",
                value=current_period.aov,
                previous_value=previous_aov,
                value_format="money",
                higher_is_better=True,
            ),
            _week_over_week_metric(
                label="Cancellation rate",
                value=current_period.cancellation_rate,
                previous_value=previous_cancellation_rate,
                value_format="percent",
                delta_unit="percentage_points",
                higher_is_better=False,
            ),
        ],
    )
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

def compute_top_customers(
    scenario: DashboardScenarioResult,
    *,
    week_start: date,
    limit: int = 10,
) -> TopCustomersResult:
    week_end = week_start + timedelta(days=6)
    customers_by_id = {
        customer.customer_id: customer
        for customer in scenario.customers
    }

    totals_by_customer: dict[str, Decimal] = {}
    counts_by_customer: dict[str, int] = {}

    for order in scenario.orders:
        local_date = _local_datetime(order.created_at).date()
        if not week_start <= local_date <= week_end:
            continue

        if order.customer_id is None or order.customer_id not in customers_by_id:
            continue

        totals_by_customer[order.customer_id] = (
            totals_by_customer.get(order.customer_id, Decimal("0")) + order.total
        )
        counts_by_customer[order.customer_id] = (
            counts_by_customer.get(order.customer_id, 0) + 1
        )

    entries = [
        TopCustomersEntry(
            customer_id=customer_id,
            customer_name=customers_by_id[customer_id].customer_name,
            order_count=counts_by_customer[customer_id],
            total_spend=total_spend,
        )
        for customer_id, total_spend in totals_by_customer.items()
    ]

    entries.sort(key=lambda item: (-item.total_spend, item.customer_name))

    return TopCustomersResult(entries=entries[:limit])


def compute_top_items(
    scenario: DashboardScenarioResult,
    *,
    week_start: date,
    limit: int = 5,
) -> TopItemsResult:
    week_end = week_start + timedelta(days=6)
    products_by_id = {
        product.product_id: product
        for product in scenario.products
    }

    quantity_by_product: dict[str, Decimal] = {}
    revenue_by_product: dict[str, Decimal] = {}

    for order in scenario.orders:
        local_date = _local_datetime(order.created_at).date()
        if not week_start <= local_date <= week_end:
            continue

        for item in order.items:
            quantity_by_product[item.product_id] = (
                quantity_by_product.get(item.product_id, Decimal("0")) + item.quantity
            )
            revenue_by_product[item.product_id] = (
                revenue_by_product.get(item.product_id, Decimal("0")) + item.line_total
            )

    entries = [
        TopItemsEntry(
            product_id=product_id,
            product_name=products_by_id[product_id].product_name
            if product_id in products_by_id
            else product_id,
            quantity=quantity,
            revenue=revenue_by_product[product_id],
        )
        for product_id, quantity in quantity_by_product.items()
    ]

    entries.sort(key=lambda item: (-item.quantity, item.product_name))

    return TopItemsResult(entries=entries[:limit])
def compute_top_items_by_category(
    scenario: DashboardScenarioResult,
    *,
    week_start: date,
    limit_per_category: int = 3,
) -> TopItemsByCategoryResult:
    week_end = week_start + timedelta(days=6)
    products_by_id = {
        product.product_id: product
        for product in scenario.products
    }

    quantity_by_product: dict[str, Decimal] = {}
    revenue_by_product: dict[str, Decimal] = {}

    for order in scenario.orders:
        local_date = _local_datetime(order.created_at).date()
        if not week_start <= local_date <= week_end:
            continue

        for item in order.items:
            quantity_by_product[item.product_id] = (
                quantity_by_product.get(item.product_id, Decimal("0")) + item.quantity
            )
            revenue_by_product[item.product_id] = (
                revenue_by_product.get(item.product_id, Decimal("0")) + item.line_total
            )

    entries_by_category: dict[str, list[TopItemsCategoryEntry]] = {}

    for product_id, quantity in quantity_by_product.items():
        product = products_by_id.get(product_id)
        category = product.category if product is not None else "unknown"
        product_name = product.product_name if product is not None else product_id

        entries_by_category.setdefault(category, []).append(
            TopItemsCategoryEntry(
                category=category,
                product_id=product_id,
                product_name=product_name,
                quantity=quantity,
                revenue=revenue_by_product[product_id],
            )
        )

    entries: list[TopItemsCategoryEntry] = []

    for category in sorted(entries_by_category):
        category_entries = entries_by_category[category]
        category_entries.sort(
            key=lambda item: (-item.quantity, item.product_name)
        )
        entries.extend(category_entries[:limit_per_category])

    return TopItemsByCategoryResult(
        entries=entries,
        week_start=week_start,
        week_end=week_end,
    )
def compute_time_of_day_heatmap(
    scenario: DashboardScenarioResult,
    *,
    today: date,
    window_days: int = 28,
) -> TimeOfDayHeatmapResult:
    window_start = today - timedelta(days=window_days - 1)
    window_end = today

    counts = {
        (weekday, hour): 0
        for weekday in range(7)
        for hour in range(24)
    }

    for order in scenario.orders:
        local_datetime = _local_datetime(order.created_at)
        local_date = local_datetime.date()

        if not window_start <= local_date <= window_end:
            continue

        counts[(local_datetime.weekday(), local_datetime.hour)] += 1

    cells = [
        TimeOfDayCell(
            weekday=weekday,
            hour=hour,
            order_count=counts[(weekday, hour)],
        )
        for weekday in range(7)
        for hour in range(24)
    ]

    return TimeOfDayHeatmapResult(
        cells=cells,
        window_start=window_start,
        window_end=window_end,
    )


def compute_product_pairs(
    scenario: DashboardScenarioResult,
    *,
    week_start: date,
    limit: int = 5,
) -> ProductPairsResult:
    week_end = week_start + timedelta(days=6)
    products_by_id = {
        product.product_id: product
        for product in scenario.products
    }
    pair_counter: Counter[tuple[str, str]] = Counter()

    for order in scenario.orders:
        local_date = _local_datetime(order.created_at).date()
        if not week_start <= local_date <= week_end:
            continue

        product_ids = sorted({item.product_id for item in order.items})
        if len(product_ids) < 2:
            continue

        for product_id_a, product_id_b in combinations(product_ids, 2):
            pair_counter[(product_id_a, product_id_b)] += 1

    sorted_pairs = sorted(
        pair_counter.items(),
        key=lambda item: (-item[1], item[0][0] + item[0][1]),
    )

    entries = [
        ProductPairEntry(
            product_id_a=product_id_a,
            product_name_a=products_by_id[product_id_a].product_name
            if product_id_a in products_by_id
            else product_id_a,
            product_id_b=product_id_b,
            product_name_b=products_by_id[product_id_b].product_name
            if product_id_b in products_by_id
            else product_id_b,
            count=count,
        )
        for (product_id_a, product_id_b), count in sorted_pairs[:limit]
    ]

    return ProductPairsResult(
        pairs=entries,
        week_start=week_start,
        limit=limit,
    )