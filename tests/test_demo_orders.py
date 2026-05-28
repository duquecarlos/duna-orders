from datetime import timedelta
from decimal import Decimal
from itertools import combinations
import pytest
from collections import Counter
from zoneinfo import ZoneInfo
from duna_orders.demo_catalog import load_demo_catalog
from duna_orders.demo_orders import (
    DEFAULT_DEMO_ANCHOR_DATE,
    DEFAULT_DEMO_ORDER_COUNT,
    DEMO_TENANT_ID,
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


def _customers():
    return build_demo_customers(seed=42)


def _products():
    return load_demo_catalog().products


def _scenario(dataset):
    return DashboardScenarioResult(
        orders=dataset.orders,
        order_items=dataset.order_items,
        customers=_customers(),
        products=_products(),
    )


def test_build_demo_order_dataset_returns_default_shape() -> None:
    dataset = build_demo_order_dataset(
        customers=_customers(),
        products=_products(),
        seed=42,
    )

    assert len(dataset.orders) == DEFAULT_DEMO_ORDER_COUNT
    assert len(dataset.order_items) >= DEFAULT_DEMO_ORDER_COUNT
    assert dataset.order_items == [
        item
        for order in dataset.orders
        for item in order.items
    ]


def test_build_demo_order_dataset_is_deterministic() -> None:
    first = build_demo_order_dataset(
        customers=_customers(),
        products=_products(),
        order_count=80,
        seed=42,
    )
    second = build_demo_order_dataset(
        customers=_customers(),
        products=_products(),
        order_count=80,
        seed=42,
    )

    assert [order.model_dump(mode="json") for order in first.orders] == [
        order.model_dump(mode="json") for order in second.orders
    ]
    assert [item.model_dump(mode="json") for item in first.order_items] == [
        item.model_dump(mode="json") for item in second.order_items
    ]


def test_build_demo_order_dataset_uses_valid_tenant_customers_and_products() -> None:
    customers = _customers()
    products = _products()

    dataset = build_demo_order_dataset(
        customers=customers,
        products=products,
        order_count=120,
        seed=42,
    )

    customer_ids = {customer.customer_id for customer in customers}
    product_ids = {product.product_id for product in products}

    assert all(order.tenant_id == DEMO_TENANT_ID for order in dataset.orders)
    assert all(item.tenant_id == DEMO_TENANT_ID for item in dataset.order_items)
    assert all(order.customer_id in customer_ids for order in dataset.orders)
    assert all(item.product_id in product_ids for item in dataset.order_items)


def test_build_demo_order_dataset_totals_match_items_and_fees() -> None:
    dataset = build_demo_order_dataset(
        customers=_customers(),
        products=_products(),
        order_count=120,
        seed=42,
    )

    for order in dataset.orders:
        item_total = sum((item.line_total for item in order.items), Decimal("0"))
        assert order.subtotal == item_total
        assert order.total == order.subtotal + order.delivery_fee + order.packaging_fee


def test_build_demo_order_dataset_has_dashboard_useful_distribution() -> None:
    dataset = build_demo_order_dataset(
        customers=_customers(),
        products=_products(),
        seed=42,
    )
    scenario = _scenario(dataset)
    today = DEFAULT_DEMO_ANCHOR_DATE
    week_start = today - timedelta(days=6)

    todays_pulse = compute_todays_pulse(scenario, today=today)
    week_trend = compute_week_trend(scenario, today=today)
    status_breakdown = compute_status_breakdown(scenario)
    customer_mix = compute_customer_mix(scenario, week_start=week_start)
    top_customers = compute_top_customers(scenario, week_start=week_start)
    top_items = compute_top_items(scenario, week_start=week_start)
    heatmap = compute_time_of_day_heatmap(scenario, today=today)
    product_pairs = compute_product_pairs(scenario, week_start=week_start)

    assert todays_pulse.orders_count > 0
    assert todays_pulse.revenue > 0
    assert len(week_trend) == 7
    assert sum(day.orders_count for day in week_trend) > 0
    assert status_breakdown.completed > 0
    assert status_breakdown.confirmed > 0
    assert status_breakdown.cancelled > 0
    assert customer_mix.new_customers > 0
    assert customer_mix.repeat_customers > 0
    assert top_customers.entries
    assert top_items.entries
    assert len(heatmap.cells) == 168
    assert sum(cell.order_count for cell in heatmap.cells) > 0
    assert product_pairs.pairs


def test_build_demo_order_dataset_rejects_empty_inputs() -> None:
    with pytest.raises(ValueError, match="At least two demo customers"):
        build_demo_order_dataset(
            customers=[],
            products=_products(),
        )

    with pytest.raises(ValueError, match="At least one active demo product"):
        build_demo_order_dataset(
            customers=_customers(),
            products=[],
        )

    with pytest.raises(ValueError, match="order_count"):
        build_demo_order_dataset(
            customers=_customers(),
            products=_products(),
            order_count=0,
        )

def test_build_demo_order_dataset_has_realistic_customer_long_tail() -> None:
    dataset = build_demo_order_dataset(
        customers=_customers(),
        products=_products(),
        seed=42,
    )

    customer_counts = Counter(order.customer_id for order in dataset.orders)
    low_frequency_orders = sum(
        count for count in customer_counts.values() if 1 <= count <= 4
    )
    one_time_customers = sum(
        1 for count in customer_counts.values() if count == 1
    )
    low_frequency_share = low_frequency_orders / len(dataset.orders)

    assert 650 <= len(customer_counts) <= 760
    assert one_time_customers >= 550
    assert 0.55 <= low_frequency_share <= 0.65
def test_build_demo_order_dataset_has_non_uniform_daily_volume() -> None:
    dataset = build_demo_order_dataset(
        customers=_customers(),
        products=_products(),
        seed=42,
    )
    timezone = ZoneInfo("America/Bogota")
    daily_counts = Counter(
        order.created_at.astimezone(timezone).date()
        for order in dataset.orders
    )
    weekday_counts = Counter(
        day.weekday()
        for day, count in daily_counts.items()
        for _ in range(count)
    )

    assert len(daily_counts) == 35
    assert max(daily_counts.values()) - min(daily_counts.values()) >= 30
    assert weekday_counts[6] > weekday_counts[0]
    assert weekday_counts[4] > weekday_counts[1]
    assert max(daily_counts.values()) >= 65
    assert min(daily_counts.values()) <= 30
def test_build_demo_order_dataset_prioritizes_signature_mains() -> None:
    dataset = build_demo_order_dataset(
        customers=_customers(),
        products=_products(),
        seed=42,
    )

    quantity_by_product_id = Counter()
    for item in dataset.order_items:
        quantity_by_product_id[item.product_id] += int(item.quantity)

    top_10_product_ids = [
        product_id
        for product_id, _ in quantity_by_product_id.most_common(10)
    ]
    top_10_main_count = sum(
        1
        for product_id in top_10_product_ids
        if product_id is not None
        and product_id.startswith(("plato-", "parrilla-", "sopa-"))
    )

    assert quantity_by_product_id["plato-bandeja-paisa"] >= 180
    assert quantity_by_product_id["plato-frijoles-garra"] >= 160
    assert quantity_by_product_id["plato-pollo-guisado-criollo"] >= 140
    assert top_10_main_count >= 4


def test_build_demo_order_dataset_has_strong_signature_pairings() -> None:
    dataset = build_demo_order_dataset(
        customers=_customers(),
        products=_products(),
        seed=42,
    )

    pair_counts = Counter()

    for order in dataset.orders:
        product_names = sorted(
            {item.product_name_snapshot for item in order.items}
        )

        for left, right in combinations(product_names, 2):
            pair_counts[(left, right)] += 1

    assert pair_counts[("Aguapanela con limón", "Bandeja paisa")] >= 60
    assert pair_counts[("Aguapanela con limón", "Fríjoles con garra")] >= 50
    assert max(pair_counts.values()) >= 60