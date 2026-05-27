from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

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
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.test_storage_contract import make_customer, make_order, make_product
from duna_orders.domain.models import OrderItem

TODAY = date(2026, 5, 26)

def _scenario(*orders):
    return DashboardScenarioResult(
        orders=list(orders),
        order_items=[item for order in orders for item in order.items],
        customers=[
            make_customer(
                "dash_",
                customer_id="cus_ana",
                phone="3001111111",
            ).model_copy(update={"customer_name": "Ana"}, deep=True),
            make_customer(
                "dash_",
                customer_id="cus_luis",
                phone="3002222222",
            ).model_copy(update={"customer_name": "Luis"}, deep=True),
            make_customer(
                "dash_",
                customer_id="cus_maria",
                phone="3003333333",
            ).model_copy(update={"customer_name": "Maria"}, deep=True),
        ],
        products=[
            make_product("dash_", product_id="prd_arepa", product_name="Arepa"),
            make_product("dash_", product_id="prd_jugo", product_name="Jugo"),
            make_product("dash_", product_id="prd_postre", product_name="Postre"),
        ],
    )

def _order(
    order_id: str,
    *,
    created_at: datetime,
    total: Decimal,
    status: str = "confirmed",
    customer_id: str | None = "cus_ana",
):
    order = make_order(
        "dash_",
        order_id=order_id,
        product_id="prd_arepa",
        status=status,
        created_at=created_at,
        customer_id=customer_id,
    )

    return order.model_copy(
        update={
            "tenant_id": DEFAULT_TEST_TENANT_ID,
            "subtotal": total,
            "total": total,
        },
        deep=True,
    )

def _item(
    *,
    order_id: str,
    product_id: str,
    product_name: str,
    quantity: Decimal,
    unit_price: Decimal = Decimal("1000"),
) -> OrderItem:
    return OrderItem(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        order_item_id=f"{order_id}_{product_id}",
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


def _order_with_items(
    order_id: str,
    *,
    created_at: datetime,
    items: list[OrderItem],
    status: str = "confirmed",
    customer_id: str | None = "cus_ana",
):
    total = sum((item.line_total for item in items), Decimal("0"))
    order = _order(
        order_id,
        created_at=created_at,
        total=total,
        status=status,
        customer_id=customer_id,
    )

    return order.model_copy(
        update={
            "items": items,
            "subtotal": total,
            "total": total,
        },
        deep=True,
    )
def _heatmap_count_by_cell(result):
    return {
        (cell.weekday, cell.hour): cell.order_count
        for cell in result.cells
    }
def test_compute_todays_pulse_counts_revenue_and_aov_for_today_orders():
    scenario = _scenario(
        _order(
            "ord_today_1",
            created_at=datetime(2026, 5, 26, 14, 0, tzinfo=timezone.utc),
            total=Decimal("12000"),
        ),
        _order(
            "ord_today_2",
            created_at=datetime(2026, 5, 26, 20, 0, tzinfo=timezone.utc),
            total=Decimal("18000"),
        ),
        _order(
            "ord_yesterday",
            created_at=datetime(2026, 5, 25, 14, 0, tzinfo=timezone.utc),
            total=Decimal("9000"),
        ),
    )

    result = compute_todays_pulse(scenario, today=TODAY)

    assert result.orders_count == 2
    assert result.revenue == Decimal("30000")
    assert result.aov == Decimal("15000")


def test_compute_todays_pulse_returns_zero_values_when_no_orders_today():
    scenario = _scenario(
        _order(
            "ord_old",
            created_at=datetime(2026, 5, 25, 14, 0, tzinfo=timezone.utc),
            total=Decimal("9000"),
        ),
    )

    result = compute_todays_pulse(scenario, today=TODAY)

    assert result.orders_count == 0
    assert result.revenue == Decimal("0")
    assert result.aov == Decimal("0")


def test_compute_week_trend_aggregates_last_seven_days_with_zero_day_gap():
    scenario = _scenario(
        _order(
            "ord_day_1",
            created_at=datetime(2026, 5, 20, 14, 0, tzinfo=timezone.utc),
            total=Decimal("10000"),
        ),
        _order(
            "ord_day_3_a",
            created_at=datetime(2026, 5, 22, 14, 0, tzinfo=timezone.utc),
            total=Decimal("12000"),
        ),
        _order(
            "ord_day_3_b",
            created_at=datetime(2026, 5, 22, 20, 0, tzinfo=timezone.utc),
            total=Decimal("8000"),
        ),
        _order(
            "ord_today",
            created_at=datetime(2026, 5, 26, 14, 0, tzinfo=timezone.utc),
            total=Decimal("15000"),
        ),
    )

    result = compute_week_trend(scenario, today=TODAY)

    assert [day.date for day in result] == [
        date(2026, 5, 20),
        date(2026, 5, 21),
        date(2026, 5, 22),
        date(2026, 5, 23),
        date(2026, 5, 24),
        date(2026, 5, 25),
        date(2026, 5, 26),
    ]
    assert [day.orders_count for day in result] == [1, 0, 2, 0, 0, 0, 1]
    assert [day.revenue for day in result] == [
        Decimal("10000"),
        Decimal("0"),
        Decimal("20000"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        Decimal("15000"),
    ]


def test_compute_status_breakdown_counts_all_buckets_including_zero_bucket():
    scenario = _scenario(
        _order(
            "ord_draft",
            created_at=datetime(2026, 5, 26, 14, 0, tzinfo=timezone.utc),
            total=Decimal("10000"),
            status="draft",
        ),
        _order(
            "ord_confirmed",
            created_at=datetime(2026, 5, 26, 15, 0, tzinfo=timezone.utc),
            total=Decimal("12000"),
            status="confirmed",
        ),
        _order(
            "ord_ready",
            created_at=datetime(2026, 5, 26, 16, 0, tzinfo=timezone.utc),
            total=Decimal("14000"),
            status="ready",
        ),
        _order(
            "ord_delivered",
            created_at=datetime(2026, 5, 26, 17, 0, tzinfo=timezone.utc),
            total=Decimal("16000"),
            status="delivered",
        ),
    )

    result = compute_status_breakdown(scenario)

    assert result.draft == 1
    assert result.confirmed == 2
    assert result.completed == 1
    assert result.cancelled == 0


def test_compute_customer_mix_returns_new_and_repeat_percentages():
    scenario = _scenario(
        _order(
            "ord_old_repeat",
            created_at=datetime(2026, 5, 10, 14, 0, tzinfo=timezone.utc),
            total=Decimal("10000"),
            customer_id="cus_ana",
        ),
        _order(
            "ord_week_repeat",
            created_at=datetime(2026, 5, 22, 14, 0, tzinfo=timezone.utc),
            total=Decimal("12000"),
            customer_id="cus_ana",
        ),
        _order(
            "ord_week_new",
            created_at=datetime(2026, 5, 23, 14, 0, tzinfo=timezone.utc),
            total=Decimal("14000"),
            customer_id="cus_luis",
        ),
    )

    result = compute_customer_mix(scenario, week_start=date(2026, 5, 20))

    assert result.new_customers == 1
    assert result.repeat_customers == 1
    assert result.new_pct == Decimal("0.5")
    assert result.repeat_pct == Decimal("0.5")


def test_compute_customer_mix_all_new_customers_returns_100_percent_new():
    scenario = _scenario(
        _order(
            "ord_new_1",
            created_at=datetime(2026, 5, 22, 14, 0, tzinfo=timezone.utc),
            total=Decimal("12000"),
            customer_id="cus_ana",
        ),
        _order(
            "ord_new_2",
            created_at=datetime(2026, 5, 23, 14, 0, tzinfo=timezone.utc),
            total=Decimal("14000"),
            customer_id="cus_luis",
        ),
    )

    result = compute_customer_mix(scenario, week_start=date(2026, 5, 20))

    assert result.new_customers == 2
    assert result.repeat_customers == 0
    assert result.new_pct == Decimal("1")
    assert result.repeat_pct == Decimal("0")


def test_compute_customer_mix_all_repeat_customers_returns_100_percent_repeat():
    scenario = _scenario(
        _order(
            "ord_old_ana",
            created_at=datetime(2026, 5, 10, 14, 0, tzinfo=timezone.utc),
            total=Decimal("10000"),
            customer_id="cus_ana",
        ),
        _order(
            "ord_old_luis",
            created_at=datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc),
            total=Decimal("12000"),
            customer_id="cus_luis",
        ),
        _order(
            "ord_week_ana",
            created_at=datetime(2026, 5, 22, 14, 0, tzinfo=timezone.utc),
            total=Decimal("14000"),
            customer_id="cus_ana",
        ),
        _order(
            "ord_week_luis",
            created_at=datetime(2026, 5, 23, 14, 0, tzinfo=timezone.utc),
            total=Decimal("16000"),
            customer_id="cus_luis",
        ),
    )

    result = compute_customer_mix(scenario, week_start=date(2026, 5, 20))

    assert result.new_customers == 0
    assert result.repeat_customers == 2
    assert result.new_pct == Decimal("0")
    assert result.repeat_pct == Decimal("1")

def test_compute_top_customers_ranks_by_spend_descending():
    scenario = _scenario(
        _order(
            "ord_ana",
            created_at=datetime(2026, 5, 22, 14, 0, tzinfo=timezone.utc),
            total=Decimal("50000"),
            customer_id="cus_ana",
        ),
        _order(
            "ord_luis",
            created_at=datetime(2026, 5, 23, 14, 0, tzinfo=timezone.utc),
            total=Decimal("70000"),
            customer_id="cus_luis",
        ),
        _order(
            "ord_maria",
            created_at=datetime(2026, 5, 24, 14, 0, tzinfo=timezone.utc),
            total=Decimal("30000"),
            customer_id="cus_maria",
        ),
    )

    result = compute_top_customers(scenario, week_start=date(2026, 5, 20))

    assert [entry.customer_name for entry in result.entries] == [
        "Luis",
        "Ana",
        "Maria",
    ]
    assert [entry.total_spend for entry in result.entries] == [
        Decimal("70000"),
        Decimal("50000"),
        Decimal("30000"),
    ]


def test_compute_top_customers_tiebreaks_by_customer_name_ascending():
    scenario = _scenario(
        _order(
            "ord_luis",
            created_at=datetime(2026, 5, 22, 14, 0, tzinfo=timezone.utc),
            total=Decimal("50000"),
            customer_id="cus_luis",
        ),
        _order(
            "ord_ana",
            created_at=datetime(2026, 5, 23, 14, 0, tzinfo=timezone.utc),
            total=Decimal("50000"),
            customer_id="cus_ana",
        ),
    )

    result = compute_top_customers(scenario, week_start=date(2026, 5, 20))

    assert [entry.customer_name for entry in result.entries] == ["Ana", "Luis"]


def test_compute_top_customers_excludes_anonymous_and_unknown_customers():
    scenario = _scenario(
        _order(
            "ord_known",
            created_at=datetime(2026, 5, 22, 14, 0, tzinfo=timezone.utc),
            total=Decimal("10000"),
            customer_id="cus_ana",
        ),
        _order(
            "ord_anonymous",
            created_at=datetime(2026, 5, 23, 14, 0, tzinfo=timezone.utc),
            total=Decimal("90000"),
            customer_id=None,
        ),
        _order(
            "ord_unknown",
            created_at=datetime(2026, 5, 24, 14, 0, tzinfo=timezone.utc),
            total=Decimal("80000"),
            customer_id="cus_unknown",
        ),
    )

    result = compute_top_customers(scenario, week_start=date(2026, 5, 20))

    assert len(result.entries) == 1
    assert result.entries[0].customer_id == "cus_ana"
    assert result.entries[0].total_spend == Decimal("10000")


def test_compute_top_customers_returns_fewer_than_limit_when_fewer_qualify():
    scenario = _scenario(
        _order(
            "ord_ana",
            created_at=datetime(2026, 5, 22, 14, 0, tzinfo=timezone.utc),
            total=Decimal("10000"),
            customer_id="cus_ana",
        ),
    )

    result = compute_top_customers(
        scenario,
        week_start=date(2026, 5, 20),
        limit=10,
    )

    assert len(result.entries) == 1


def test_compute_top_customers_empty_input_returns_empty_result():
    result = compute_top_customers(
        _scenario(),
        week_start=date(2026, 5, 20),
    )

    assert result.entries == []


def test_compute_top_customers_respects_time_window():
    scenario = _scenario(
        _order(
            "ord_old_high",
            created_at=datetime(2026, 5, 10, 14, 0, tzinfo=timezone.utc),
            total=Decimal("90000"),
            customer_id="cus_luis",
        ),
        _order(
            "ord_week_low",
            created_at=datetime(2026, 5, 22, 14, 0, tzinfo=timezone.utc),
            total=Decimal("10000"),
            customer_id="cus_ana",
        ),
    )

    result = compute_top_customers(scenario, week_start=date(2026, 5, 20))

    assert len(result.entries) == 1
    assert result.entries[0].customer_id == "cus_ana"
    assert result.entries[0].total_spend == Decimal("10000")


def test_compute_top_items_ranks_by_quantity_descending():
    scenario = _scenario(
        _order_with_items(
            "ord_items",
            created_at=datetime(2026, 5, 22, 14, 0, tzinfo=timezone.utc),
            items=[
                _item(
                    order_id="ord_items",
                    product_id="prd_arepa",
                    product_name="Arepa",
                    quantity=Decimal("3"),
                ),
                _item(
                    order_id="ord_items",
                    product_id="prd_jugo",
                    product_name="Jugo",
                    quantity=Decimal("5"),
                ),
                _item(
                    order_id="ord_items",
                    product_id="prd_postre",
                    product_name="Postre",
                    quantity=Decimal("1"),
                ),
            ],
        ),
    )

    result = compute_top_items(scenario, week_start=date(2026, 5, 20))

    assert [entry.product_name for entry in result.entries] == [
        "Jugo",
        "Arepa",
        "Postre",
    ]
    assert [entry.quantity for entry in result.entries] == [
        Decimal("5"),
        Decimal("3"),
        Decimal("1"),
    ]


def test_compute_top_items_tiebreaks_by_product_name_ascending():
    scenario = _scenario(
        _order_with_items(
            "ord_tie",
            created_at=datetime(2026, 5, 22, 14, 0, tzinfo=timezone.utc),
            items=[
                _item(
                    order_id="ord_tie",
                    product_id="prd_jugo",
                    product_name="Jugo",
                    quantity=Decimal("2"),
                ),
                _item(
                    order_id="ord_tie",
                    product_id="prd_arepa",
                    product_name="Arepa",
                    quantity=Decimal("2"),
                ),
            ],
        ),
    )

    result = compute_top_items(scenario, week_start=date(2026, 5, 20))

    assert [entry.product_name for entry in result.entries] == ["Arepa", "Jugo"]


def test_compute_top_items_includes_missing_catalog_product_with_id_fallback():
    scenario = _scenario(
        _order_with_items(
            "ord_missing_product",
            created_at=datetime(2026, 5, 22, 14, 0, tzinfo=timezone.utc),
            items=[
                _item(
                    order_id="ord_missing_product",
                    product_id="prd_missing",
                    product_name="Deleted product",
                    quantity=Decimal("4"),
                ),
            ],
        ),
    )

    result = compute_top_items(scenario, week_start=date(2026, 5, 20))

    assert len(result.entries) == 1
    assert result.entries[0].product_id == "prd_missing"
    assert result.entries[0].product_name == "prd_missing"
    assert result.entries[0].quantity == Decimal("4")


def test_compute_top_items_returns_fewer_than_limit_when_fewer_qualify():
    scenario = _scenario(
        _order_with_items(
            "ord_single_item",
            created_at=datetime(2026, 5, 22, 14, 0, tzinfo=timezone.utc),
            items=[
                _item(
                    order_id="ord_single_item",
                    product_id="prd_arepa",
                    product_name="Arepa",
                    quantity=Decimal("1"),
                ),
            ],
        ),
    )

    result = compute_top_items(
        scenario,
        week_start=date(2026, 5, 20),
        limit=5,
    )

    assert len(result.entries) == 1


def test_compute_top_items_empty_input_returns_empty_result():
    result = compute_top_items(
        _scenario(),
        week_start=date(2026, 5, 20),
    )

    assert result.entries == []


def test_compute_top_items_respects_time_window():
    scenario = _scenario(
        _order_with_items(
            "ord_old_items",
            created_at=datetime(2026, 5, 10, 14, 0, tzinfo=timezone.utc),
            items=[
                _item(
                    order_id="ord_old_items",
                    product_id="prd_jugo",
                    product_name="Jugo",
                    quantity=Decimal("99"),
                ),
            ],
        ),
        _order_with_items(
            "ord_week_items",
            created_at=datetime(2026, 5, 22, 14, 0, tzinfo=timezone.utc),
            items=[
                _item(
                    order_id="ord_week_items",
                    product_id="prd_arepa",
                    product_name="Arepa",
                    quantity=Decimal("1"),
                ),
            ],
        ),
    )

    result = compute_top_items(scenario, week_start=date(2026, 5, 20))

    assert len(result.entries) == 1
    assert result.entries[0].product_id == "prd_arepa"
    assert result.entries[0].quantity == Decimal("1")

def test_compute_time_of_day_heatmap_counts_by_weekday_and_hour():
    scenario = _scenario(
        _order(
            "ord_tuesday_morning",
            created_at=datetime(2026, 5, 26, 14, 0, tzinfo=timezone.utc),
            total=Decimal("10000"),
        ),
        _order(
            "ord_tuesday_morning_2",
            created_at=datetime(2026, 5, 26, 14, 30, tzinfo=timezone.utc),
            total=Decimal("12000"),
        ),
        _order(
            "ord_sunday_afternoon",
            created_at=datetime(2026, 5, 24, 20, 0, tzinfo=timezone.utc),
            total=Decimal("14000"),
        ),
    )

    result = compute_time_of_day_heatmap(scenario, today=TODAY)
    counts = _heatmap_count_by_cell(result)

    assert len(result.cells) == 168
    assert result.window_start == date(2026, 4, 29)
    assert result.window_end == TODAY
    assert counts[(1, 9)] == 2
    assert counts[(6, 15)] == 1
    assert counts[(0, 0)] == 0


def test_compute_time_of_day_heatmap_uses_bogota_timezone_boundary():
    scenario = _scenario(
        _order(
            "ord_utc_tuesday_local_monday",
            created_at=datetime(2026, 5, 26, 2, 30, tzinfo=timezone.utc),
            total=Decimal("10000"),
        ),
    )

    result = compute_time_of_day_heatmap(scenario, today=TODAY)
    counts = _heatmap_count_by_cell(result)

    assert counts[(0, 21)] == 1
    assert counts[(1, 2)] == 0


def test_compute_time_of_day_heatmap_respects_window_and_includes_today():
    scenario = _scenario(
        _order(
            "ord_before_window",
            created_at=datetime(2026, 4, 28, 14, 0, tzinfo=timezone.utc),
            total=Decimal("10000"),
        ),
        _order(
            "ord_today",
            created_at=datetime(2026, 5, 26, 14, 0, tzinfo=timezone.utc),
            total=Decimal("12000"),
        ),
    )

    result = compute_time_of_day_heatmap(scenario, today=TODAY)
    counts = _heatmap_count_by_cell(result)

    assert len(result.cells) == 168
    assert sum(counts.values()) == 1
    assert counts[(1, 9)] == 1


def test_compute_time_of_day_heatmap_empty_input_returns_zero_grid():
    result = compute_time_of_day_heatmap(_scenario(), today=TODAY)
    counts = _heatmap_count_by_cell(result)

    assert len(result.cells) == 168
    assert all(count == 0 for count in counts.values())


def test_compute_product_pairs_counts_shared_pairs():
    scenario = _scenario(
        _order_with_items(
            "ord_pair_1",
            created_at=datetime(2026, 5, 22, 14, 0, tzinfo=timezone.utc),
            items=[
                _item(
                    order_id="ord_pair_1",
                    product_id="prd_arepa",
                    product_name="Arepa",
                    quantity=Decimal("1"),
                ),
                _item(
                    order_id="ord_pair_1",
                    product_id="prd_jugo",
                    product_name="Jugo",
                    quantity=Decimal("1"),
                ),
                _item(
                    order_id="ord_pair_1",
                    product_id="prd_postre",
                    product_name="Postre",
                    quantity=Decimal("1"),
                ),
            ],
        ),
        _order_with_items(
            "ord_pair_2",
            created_at=datetime(2026, 5, 23, 14, 0, tzinfo=timezone.utc),
            items=[
                _item(
                    order_id="ord_pair_2",
                    product_id="prd_arepa",
                    product_name="Arepa",
                    quantity=Decimal("1"),
                ),
                _item(
                    order_id="ord_pair_2",
                    product_id="prd_jugo",
                    product_name="Jugo",
                    quantity=Decimal("1"),
                ),
            ],
        ),
    )

    result = compute_product_pairs(scenario, week_start=date(2026, 5, 20))

    assert [(pair.product_id_a, pair.product_id_b, pair.count) for pair in result.pairs] == [
        ("prd_arepa", "prd_jugo", 2),
        ("prd_arepa", "prd_postre", 1),
        ("prd_jugo", "prd_postre", 1),
    ]


def test_compute_product_pairs_tiebreaks_by_concatenated_product_ids():
    scenario = _scenario(
        _order_with_items(
            "ord_tie_pair_1",
            created_at=datetime(2026, 5, 22, 14, 0, tzinfo=timezone.utc),
            items=[
                _item(
                    order_id="ord_tie_pair_1",
                    product_id="prd_arepa",
                    product_name="Arepa",
                    quantity=Decimal("1"),
                ),
                _item(
                    order_id="ord_tie_pair_1",
                    product_id="prd_postre",
                    product_name="Postre",
                    quantity=Decimal("1"),
                ),
            ],
        ),
        _order_with_items(
            "ord_tie_pair_2",
            created_at=datetime(2026, 5, 23, 14, 0, tzinfo=timezone.utc),
            items=[
                _item(
                    order_id="ord_tie_pair_2",
                    product_id="prd_jugo",
                    product_name="Jugo",
                    quantity=Decimal("1"),
                ),
                _item(
                    order_id="ord_tie_pair_2",
                    product_id="prd_postre",
                    product_name="Postre",
                    quantity=Decimal("1"),
                ),
            ],
        ),
    )

    result = compute_product_pairs(scenario, week_start=date(2026, 5, 20))

    assert [(pair.product_id_a, pair.product_id_b) for pair in result.pairs] == [
        ("prd_arepa", "prd_postre"),
        ("prd_jugo", "prd_postre"),
    ]


def test_compute_product_pairs_ignores_single_product_orders_and_deduplicates_products():
    scenario = _scenario(
        _order_with_items(
            "ord_single_product",
            created_at=datetime(2026, 5, 22, 14, 0, tzinfo=timezone.utc),
            items=[
                _item(
                    order_id="ord_single_product",
                    product_id="prd_arepa",
                    product_name="Arepa",
                    quantity=Decimal("1"),
                ),
            ],
        ),
        _order_with_items(
            "ord_duplicate_product",
            created_at=datetime(2026, 5, 23, 14, 0, tzinfo=timezone.utc),
            items=[
                _item(
                    order_id="ord_duplicate_product",
                    product_id="prd_arepa",
                    product_name="Arepa",
                    quantity=Decimal("1"),
                ),
                _item(
                    order_id="ord_duplicate_product",
                    product_id="prd_arepa",
                    product_name="Arepa",
                    quantity=Decimal("2"),
                ),
                _item(
                    order_id="ord_duplicate_product",
                    product_id="prd_jugo",
                    product_name="Jugo",
                    quantity=Decimal("1"),
                ),
            ],
        ),
    )

    result = compute_product_pairs(scenario, week_start=date(2026, 5, 20))

    assert len(result.pairs) == 1
    assert result.pairs[0].product_id_a == "prd_arepa"
    assert result.pairs[0].product_id_b == "prd_jugo"
    assert result.pairs[0].count == 1


def test_compute_product_pairs_uses_canonical_ordering():
    scenario = _scenario(
        _order_with_items(
            "ord_b_a",
            created_at=datetime(2026, 5, 22, 14, 0, tzinfo=timezone.utc),
            items=[
                _item(
                    order_id="ord_b_a",
                    product_id="prd_jugo",
                    product_name="Jugo",
                    quantity=Decimal("1"),
                ),
                _item(
                    order_id="ord_b_a",
                    product_id="prd_arepa",
                    product_name="Arepa",
                    quantity=Decimal("1"),
                ),
            ],
        ),
        _order_with_items(
            "ord_a_b",
            created_at=datetime(2026, 5, 23, 14, 0, tzinfo=timezone.utc),
            items=[
                _item(
                    order_id="ord_a_b",
                    product_id="prd_arepa",
                    product_name="Arepa",
                    quantity=Decimal("1"),
                ),
                _item(
                    order_id="ord_a_b",
                    product_id="prd_jugo",
                    product_name="Jugo",
                    quantity=Decimal("1"),
                ),
            ],
        ),
    )

    result = compute_product_pairs(scenario, week_start=date(2026, 5, 20))

    assert len(result.pairs) == 1
    assert result.pairs[0].product_id_a == "prd_arepa"
    assert result.pairs[0].product_id_b == "prd_jugo"
    assert result.pairs[0].count == 2


def test_compute_product_pairs_returns_fewer_than_limit_and_empty_input_is_empty():
    scenario = _scenario(
        _order_with_items(
            "ord_one_pair",
            created_at=datetime(2026, 5, 22, 14, 0, tzinfo=timezone.utc),
            items=[
                _item(
                    order_id="ord_one_pair",
                    product_id="prd_arepa",
                    product_name="Arepa",
                    quantity=Decimal("1"),
                ),
                _item(
                    order_id="ord_one_pair",
                    product_id="prd_jugo",
                    product_name="Jugo",
                    quantity=Decimal("1"),
                ),
            ],
        ),
    )

    result = compute_product_pairs(
        scenario,
        week_start=date(2026, 5, 20),
        limit=5,
    )
    empty_result = compute_product_pairs(
        _scenario(),
        week_start=date(2026, 5, 20),
    )

    assert len(result.pairs) == 1
    assert empty_result.pairs == []


def test_compute_product_pairs_respects_time_window():
    scenario = _scenario(
        _order_with_items(
            "ord_old_pair",
            created_at=datetime(2026, 5, 10, 14, 0, tzinfo=timezone.utc),
            items=[
                _item(
                    order_id="ord_old_pair",
                    product_id="prd_arepa",
                    product_name="Arepa",
                    quantity=Decimal("1"),
                ),
                _item(
                    order_id="ord_old_pair",
                    product_id="prd_jugo",
                    product_name="Jugo",
                    quantity=Decimal("1"),
                ),
            ],
        ),
        _order_with_items(
            "ord_week_pair",
            created_at=datetime(2026, 5, 22, 14, 0, tzinfo=timezone.utc),
            items=[
                _item(
                    order_id="ord_week_pair",
                    product_id="prd_arepa",
                    product_name="Arepa",
                    quantity=Decimal("1"),
                ),
                _item(
                    order_id="ord_week_pair",
                    product_id="prd_postre",
                    product_name="Postre",
                    quantity=Decimal("1"),
                ),
            ],
        ),
    )

    result = compute_product_pairs(scenario, week_start=date(2026, 5, 20))

    assert len(result.pairs) == 1
    assert result.pairs[0].product_id_a == "prd_arepa"
    assert result.pairs[0].product_id_b == "prd_postre"


def test_compute_product_pairs_uses_product_id_fallback_for_missing_catalog_product():
    scenario = _scenario(
        _order_with_items(
            "ord_missing_pair",
            created_at=datetime(2026, 5, 22, 14, 0, tzinfo=timezone.utc),
            items=[
                _item(
                    order_id="ord_missing_pair",
                    product_id="prd_arepa",
                    product_name="Arepa",
                    quantity=Decimal("1"),
                ),
                _item(
                    order_id="ord_missing_pair",
                    product_id="prd_missing",
                    product_name="Deleted product",
                    quantity=Decimal("1"),
                ),
            ],
        ),
    )

    result = compute_product_pairs(scenario, week_start=date(2026, 5, 20))

    assert len(result.pairs) == 1
    assert result.pairs[0].product_id_a == "prd_arepa"
    assert result.pairs[0].product_name_a == "Arepa"
    assert result.pairs[0].product_id_b == "prd_missing"
    assert result.pairs[0].product_name_b == "prd_missing"