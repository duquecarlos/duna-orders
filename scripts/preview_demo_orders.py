from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from duna_orders.demo_catalog import load_demo_catalog
from duna_orders.demo_orders import (
    DEFAULT_DEMO_ANCHOR_DATE,
    DEFAULT_DEMO_ORDER_COUNT,
    DEMO_TIMEZONE,
    build_demo_order_dataset,
)
from duna_orders.services.dashboard import (
    DashboardScenarioResult,
    compute_customer_mix,
    compute_product_pairs,
    compute_status_breakdown,
    compute_time_of_day_heatmap,
    compute_todays_pulse,
    compute_top_customers,
    compute_top_items,
    compute_week_trend,
)
from scripts.seed_demo_data import build_demo_customers


WEEKDAY_LABELS = {
    0: "Mon",
    1: "Tue",
    2: "Wed",
    3: "Thu",
    4: "Fri",
    5: "Sat",
    6: "Sun",
}


def _money(value: Decimal) -> str:
    return f"${value:,.0f} COP"


def _local_date_range(scenario: DashboardScenarioResult) -> tuple[str, str]:
    timezone = ZoneInfo(DEMO_TIMEZONE)
    local_dates = [
        order.created_at.astimezone(timezone).date()
        for order in scenario.orders
    ]

    return (
        min(local_dates).isoformat(),
        max(local_dates).isoformat(),
    )


def _status_counts(scenario: DashboardScenarioResult) -> Counter[str]:
    return Counter(order.status for order in scenario.orders)


def _print_week_trend(scenario: DashboardScenarioResult) -> None:
    trend = compute_week_trend(
        scenario,
        today=DEFAULT_DEMO_ANCHOR_DATE,
    )

    print("\nLast 7 days:")
    for day in trend:
        print(
            f"  {day.date.isoformat()}: "
            f"{day.orders_count} orders, {_money(day.revenue)}"
        )


def _print_top_customers(scenario: DashboardScenarioResult) -> None:
    result = compute_top_customers(
        scenario,
        week_start=DEFAULT_DEMO_ANCHOR_DATE - timedelta(days=6),
        limit=5,
    )

    print("\nTop customers this week:")
    for index, entry in enumerate(result.entries, start=1):
        print(
            f"  {index}. {entry.customer_name}: "
            f"{entry.order_count} orders, {_money(entry.total_spend)}"
        )


def _print_top_items(scenario: DashboardScenarioResult) -> None:
    result = compute_top_items(
        scenario,
        week_start=DEFAULT_DEMO_ANCHOR_DATE - timedelta(days=6),
        limit=5,
    )

    print("\nTop items this week:")
    for index, entry in enumerate(result.entries, start=1):
        print(
            f"  {index}. {entry.product_name}: "
            f"{entry.quantity} units, {_money(entry.revenue)}"
        )


def _print_product_pairs(scenario: DashboardScenarioResult) -> None:
    result = compute_product_pairs(
        scenario,
        week_start=DEFAULT_DEMO_ANCHOR_DATE - timedelta(days=6),
        limit=5,
    )

    print("\nFrequent item pairs this week:")
    for index, pair in enumerate(result.pairs, start=1):
        print(
            f"  {index}. {pair.product_name_a} + {pair.product_name_b}: "
            f"{pair.count} orders"
        )


def _print_heatmap_peaks(scenario: DashboardScenarioResult) -> None:
    heatmap = compute_time_of_day_heatmap(
        scenario,
        today=DEFAULT_DEMO_ANCHOR_DATE,
    )

    top_cells = sorted(
        [cell for cell in heatmap.cells if cell.order_count > 0],
        key=lambda cell: (-cell.order_count, cell.weekday, cell.hour),
    )[:5]

    print("\nTop time-of-day cells, last 28 days:")
    for cell in top_cells:
        print(
            f"  {WEEKDAY_LABELS[cell.weekday]} {cell.hour:02d}:00: "
            f"{cell.order_count} orders"
        )


def print_demo_order_summary(
    *,
    order_count: int,
    seed: int,
) -> None:
    customers = build_demo_customers(seed=seed)
    catalog = load_demo_catalog()
    dataset = build_demo_order_dataset(
        customers=customers,
        products=catalog.products,
        order_count=order_count,
        seed=seed,
    )

    scenario = DashboardScenarioResult(
        orders=dataset.orders,
        order_items=dataset.order_items,
        customers=customers,
        products=catalog.products,
    )

    range_start, range_end = _local_date_range(scenario)
    status_counts = _status_counts(scenario)
    todays_pulse = compute_todays_pulse(
        scenario,
        today=DEFAULT_DEMO_ANCHOR_DATE,
    )
    status_breakdown = compute_status_breakdown(scenario)
    customer_mix = compute_customer_mix(
        scenario,
        week_start=DEFAULT_DEMO_ANCHOR_DATE - timedelta(days=6),
    )

    print("Demo order dataset preview")
    print("==========================")
    print(f"Seed: {seed}")
    print(f"Anchor date: {DEFAULT_DEMO_ANCHOR_DATE.isoformat()}")
    print(f"Local date range: {range_start} to {range_end}")
    print(f"Customers: {len(customers)}")
    print(f"Products: {len(catalog.products)}")
    print(f"Orders: {len(dataset.orders)}")
    print(f"Order items: {len(dataset.order_items)}")

    print("\nRaw status counts:")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")

    print("\nDashboard status buckets:")
    print(f"  Draft: {status_breakdown.draft}")
    print(f"  Confirmed/in progress: {status_breakdown.confirmed}")
    print(f"  Completed: {status_breakdown.completed}")
    print(f"  Cancelled: {status_breakdown.cancelled}")

    print("\nToday's pulse:")
    print(f"  Orders: {todays_pulse.orders_count}")
    print(f"  Revenue: {_money(todays_pulse.revenue)}")
    print(f"  AOV: {_money(todays_pulse.aov)}")

    print("\nCustomer mix this week:")
    print(f"  New customers: {customer_mix.new_customers}")
    print(f"  Repeat customers: {customer_mix.repeat_customers}")

    _print_week_trend(scenario)
    _print_top_customers(scenario)
    _print_top_items(scenario)
    _print_product_pairs(scenario)
    _print_heatmap_peaks(scenario)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview deterministic demo orders without writing to Sheets."
    )
    parser.add_argument(
        "--orders",
        type=int,
        default=DEFAULT_DEMO_ORDER_COUNT,
        help="Number of demo orders to generate. Defaults to 1500.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Seed for deterministic demo order generation.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        print_demo_order_summary(
            order_count=args.orders,
            seed=args.seed,
        )
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())