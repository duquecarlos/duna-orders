from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Engine, event

from duna_orders.domain.models import OrderItem
from duna_orders.services.dashboard import (
    compute_customer_mix,
    compute_product_pairs,
    compute_week_over_week,
    compute_time_of_day_heatmap,
    compute_todays_pulse,
    compute_todays_status_strip,
    compute_top_customers,
    compute_top_items_by_category,
    compute_week_trend,
    resolve_reference_date,
)
from duna_orders.services.dashboard_read_scenario import (
    LOCKED_DASHBOARD_READ_BUDGET,
    run_locked_dashboard_read_scenario,
)
from duna_orders.storage.postgres import PostgresStorage
from duna_orders.storage.postgres_base import Base
from duna_orders.storage.postgres_session import make_engine, make_session_factory
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.test_storage_contract import make_customer, make_order, make_product


NOW = datetime(2026, 5, 26, 15, 0, tzinfo=timezone.utc)
TIMEZONE_NAME = "America/Bogota"


@contextmanager
def _count_select_statements(engine: Engine) -> Iterator[list[str]]:
    statements: list[str] = []

    def before_cursor_execute(
        conn,
        cursor,
        statement,
        parameters,
        context,
        executemany,
    ) -> None:
        del conn
        del cursor
        del parameters
        del context
        del executemany

        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", before_cursor_execute)

    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)


def _sqlite_postgres_storage(tmp_path: Path) -> tuple[PostgresStorage, Engine]:
    database_path = tmp_path / "postgres_dashboard_query_budget.db"
    engine = make_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)

    return PostgresStorage(make_session_factory(engine)), engine


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


def _seed_dashboard_budget_data(storage: PostgresStorage) -> None:
    products = [
        make_product(
            "pg_budget_",
            product_id="pg_budget_prd_arepa",
            product_name="Arepa",
            unit_price=Decimal("8000"),
        ),
        make_product(
            "pg_budget_",
            product_id="pg_budget_prd_jugo",
            product_name="Jugo",
            unit_price=Decimal("5000"),
        ),
        make_product(
            "pg_budget_",
            product_id="pg_budget_prd_postre",
            product_name="Postre",
            unit_price=Decimal("7000"),
        ),
    ]

    customers = [
        make_customer(
            "pg_budget_",
            customer_id="pg_budget_cus_ana",
            phone="3001111111",
        ),
        make_customer(
            "pg_budget_",
            customer_id="pg_budget_cus_luis",
            phone="3002222222",
        ),
    ]

    for product in products:
        storage.upsert_product(product)

    for customer in customers:
        storage.create_customer(customer)

    order_today = make_order(
        "pg_budget_",
        order_id="pg_budget_ord_today",
        product_id="pg_budget_prd_arepa",
        status="confirmed",
        created_at=NOW,
        customer_id="pg_budget_cus_ana",
    )
    order_today = order_today.model_copy(
        update={
            "items": [
                order_today.items[0],
                _extra_item(
                    order_id=order_today.order_id,
                    product_id="pg_budget_prd_jugo",
                    product_name="Jugo",
                ),
            ],
            "subtotal": Decimal("13000"),
            "total": Decimal("13000"),
        },
        deep=True,
    )

    order_week = make_order(
        "pg_budget_",
        order_id="pg_budget_ord_week",
        product_id="pg_budget_prd_postre",
        status="delivered",
        created_at=NOW - timedelta(days=2),
        customer_id="pg_budget_cus_luis",
    )
    order_week = order_week.model_copy(
        update={
            "items": [
                order_week.items[0],
                _extra_item(
                    order_id=order_week.order_id,
                    product_id="pg_budget_prd_jugo",
                    product_name="Jugo",
                ),
            ],
            "subtotal": Decimal("12000"),
            "total": Decimal("12000"),
        },
        deep=True,
    )

    storage.create_order(order_today)
    storage.create_order(order_week)


def _compute_locked_dashboard_widgets(
    storage: PostgresStorage,
) -> dict[str, object]:
    scenario = run_locked_dashboard_read_scenario(
        storage,
        tenant_id=DEFAULT_TEST_TENANT_ID,
        now=NOW,
        timezone_name=TIMEZONE_NAME,
    )
    reference_date = resolve_reference_date(
        scenario.orders,
        "runtime",
        today=NOW.date(),
    )
    week_start = reference_date - timedelta(days=6)

    return {
        "todays_pulse": compute_todays_pulse(scenario, today=reference_date),
        "todays_status_strip": compute_todays_status_strip(
            scenario,
            today=reference_date,
        ),
        "week_trend": compute_week_trend(scenario, today=reference_date),
        "week_over_week": compute_week_over_week(
            scenario,
            reference_date=reference_date,
        ),
        "customer_mix": compute_customer_mix(scenario, week_start=week_start),
        "top_customers": compute_top_customers(scenario, week_start=week_start),
        "top_items_by_category": compute_top_items_by_category(
            scenario,
            week_start=week_start,
        ),
        "time_of_day_heatmap": compute_time_of_day_heatmap(
            scenario,
            today=reference_date,
        ),
        "product_pairs": compute_product_pairs(scenario, week_start=week_start),
    }


def test_locked_dashboard_postgres_render_stays_within_select_budget(
    tmp_path: Path,
) -> None:
    storage, engine = _sqlite_postgres_storage(tmp_path)
    _seed_dashboard_budget_data(storage)

    with _count_select_statements(engine) as select_statements:
        widgets = _compute_locked_dashboard_widgets(storage)

    assert len(select_statements) <= LOCKED_DASHBOARD_READ_BUDGET
    assert len(select_statements) == 4

    assert widgets["todays_pulse"].orders_count == 1
    assert len(widgets["week_trend"]) == 7
    assert widgets["week_over_week"].current_period.orders_count >= 1
    assert widgets["customer_mix"].repeat_customers >= 0
    assert widgets["top_customers"].entries
    assert widgets["top_items_by_category"].entries
    assert widgets["time_of_day_heatmap"].cells
    assert widgets["product_pairs"].pairs