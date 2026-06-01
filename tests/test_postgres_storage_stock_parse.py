from decimal import Decimal

import pytest

from duna_orders.domain.models import ParseLogEntry, StockMovement
from duna_orders.storage.postgres import PostgresStorage
from duna_orders.storage.postgres_base import Base
from duna_orders.storage.postgres_session import make_engine, make_session_factory
from tests.conftest import DEFAULT_TEST_TENANT_ID


@pytest.fixture
def postgres_storage(tmp_path) -> PostgresStorage:
    database_path = tmp_path / "postgres_storage_stock_parse_test.db"
    engine = make_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)

    return PostgresStorage(make_session_factory(engine))


def make_stock_movement(
    *,
    stock_movement_id: str = "mov_test",
    product_id: str = "prd_test",
    quantity_delta: Decimal = Decimal("-2"),
    reason: str = "sale",
) -> StockMovement:
    return StockMovement(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        stock_movement_id=stock_movement_id,
        product_id=product_id,
        quantity_delta=quantity_delta,
        reason=reason,
        reference_id="ord_test",
        notes="stock note",
        created_by="operator-test",
    )


def make_parse_log_entry(
    *,
    parse_id: str = "prs_test",
    success: bool = True,
    error: str | None = None,
) -> ParseLogEntry:
    return ParseLogEntry(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        parse_id=parse_id,
        raw_message="me regala 2 pollos",
        parsed_json='{"items":[]}',
        model="test-model",
        prompt_version="test-prompt-v1",
        latency_ms=120,
        success=success,
        error=error,
    )


def test_append_and_list_stock_movements_filters_by_product(
    postgres_storage: PostgresStorage,
):
    sale = make_stock_movement(
        stock_movement_id="mov_sale",
        product_id="prd_main",
        quantity_delta=Decimal("-2"),
        reason="sale",
    )
    reversal = make_stock_movement(
        stock_movement_id="mov_reversal",
        product_id="prd_main",
        quantity_delta=Decimal("2"),
        reason="reversal",
    )
    other_product = make_stock_movement(
        stock_movement_id="mov_other",
        product_id="prd_other",
        quantity_delta=Decimal("-1"),
        reason="sale",
    )

    saved_sale = postgres_storage.append_stock_movement(sale)
    postgres_storage.append_stock_movement(reversal)
    postgres_storage.append_stock_movement(other_product)

    movements = postgres_storage.list_stock_movements(product_id="prd_main")
    net_quantity = sum(movement.quantity_delta for movement in movements)

    assert saved_sale.stock_movement_id == "mov_sale"
    assert saved_sale.notes == "stock note"
    assert [movement.stock_movement_id for movement in movements] == [
        "mov_reversal",
        "mov_sale",
    ]
    assert net_quantity == Decimal("0")


def test_list_stock_movements_without_filter_returns_all(
    postgres_storage: PostgresStorage,
):
    postgres_storage.append_stock_movement(
        make_stock_movement(stock_movement_id="mov_a", product_id="prd_a")
    )
    postgres_storage.append_stock_movement(
        make_stock_movement(stock_movement_id="mov_b", product_id="prd_b")
    )

    movement_ids = [
        movement.stock_movement_id
        for movement in postgres_storage.list_stock_movements()
    ]

    assert movement_ids == ["mov_a", "mov_b"]


def test_append_stock_movement_raises_on_duplicate_id(
    postgres_storage: PostgresStorage,
):
    movement = make_stock_movement()

    postgres_storage.append_stock_movement(movement)

    with pytest.raises(ValueError):
        postgres_storage.append_stock_movement(movement)


def test_append_parse_log_persists_entry(postgres_storage: PostgresStorage):
    entry = make_parse_log_entry()

    saved_entry = postgres_storage.append_parse_log(entry)

    assert saved_entry.parse_id == entry.parse_id
    assert saved_entry.raw_message == entry.raw_message
    assert saved_entry.parsed_json == entry.parsed_json
    assert saved_entry.model == "test-model"
    assert saved_entry.prompt_version == "test-prompt-v1"
    assert saved_entry.latency_ms == 120
    assert saved_entry.success is True
    assert saved_entry.error is None


def test_append_parse_log_persists_error_entry(postgres_storage: PostgresStorage):
    entry = make_parse_log_entry(
        parse_id="prs_error",
        success=False,
        error="schema invalid",
    )

    saved_entry = postgres_storage.append_parse_log(entry)

    assert saved_entry.parse_id == "prs_error"
    assert saved_entry.success is False
    assert saved_entry.error == "schema invalid"


def test_append_parse_log_raises_on_duplicate_id(postgres_storage: PostgresStorage):
    entry = make_parse_log_entry(parse_id="prs_fixed_id")

    postgres_storage.append_parse_log(entry)

    with pytest.raises(ValueError):
        postgres_storage.append_parse_log(entry)