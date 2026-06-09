from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from duna_orders.domain.models import Order, OrderItem, Product, StockMovement
from duna_orders.services.orders import OrderService
from duna_orders.storage.exceptions import (
    DuplicateStockMovementError,
    StorageOrderStatusMismatchError,
)
from duna_orders.storage.order_confirmation import PostgresAtomicOrderConfirmationStore
from duna_orders.storage.order_lifecycle import PostgresOrderLifecycleStore
from duna_orders.storage.postgres import PostgresStorage
from duna_orders.storage.postgres_base import Base
from duna_orders.storage.postgres_session import make_engine, make_session_factory
from tests.conftest import DEFAULT_TEST_TENANT_ID


PRODUCT_ID = "prd_empanada"
ORDER_ID = "ord_atomic"


def _session_factory(tmp_path: Path):
    database_path = tmp_path / "order_confirmation.db"
    engine = make_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def _product(*, current_stock: Decimal = Decimal("10")) -> Product:
    return Product(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        product_id=PRODUCT_ID,
        product_name="Empanada",
        unit_price=Decimal("3000"),
        current_stock=current_stock,
    )


def _order(*, status: str = "approved") -> Order:
    item = OrderItem(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        order_item_id="oit_atomic",
        order_id=ORDER_ID,
        product_id=PRODUCT_ID,
        product_name_snapshot="Empanada",
        quantity=Decimal("2"),
        unit_price_snapshot=Decimal("3000"),
        line_total=Decimal("6000"),
        validation_status="ok",
    )
    return Order(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        order_id=ORDER_ID,
        raw_message="Dos empanadas",
        status=status,
        items=[item],
        subtotal=Decimal("6000"),
        total=Decimal("6000"),
    )


def _seed_approved_order(tmp_path: Path):
    session_factory = _session_factory(tmp_path)
    storage = PostgresStorage(session_factory)
    storage.upsert_product(_product())
    storage.create_order(_order())
    confirmation_store = PostgresAtomicOrderConfirmationStore(session_factory)
    lifecycle_store = PostgresOrderLifecycleStore(session_factory)
    service = OrderService(
        storage,
        atomic_confirmation_store=confirmation_store,
    )
    return storage, lifecycle_store, service


def test_confirm_approved_order_postgres_happy_path(
    tmp_path: Path,
) -> None:
    storage, lifecycle_store, service = _seed_approved_order(tmp_path)
    confirmed_at = datetime(2026, 6, 9, 13, 0, tzinfo=timezone.utc)

    order = service.confirm_approved_order(
        order_id=ORDER_ID,
        tenant_id=DEFAULT_TEST_TENANT_ID,
        confirmed_at=confirmed_at,
    )

    saved_order = storage.get_order(ORDER_ID)
    product = storage.get_product(PRODUCT_ID)
    movements = storage.list_stock_movements(product_id=PRODUCT_ID)
    transitions = lifecycle_store.list_order_status_transitions(
        order_id=ORDER_ID,
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )

    assert order.status == "confirmed"
    assert order.confirmed_at == confirmed_at
    assert saved_order is not None
    assert saved_order.status == "confirmed"
    assert saved_order.confirmed_at == confirmed_at
    assert product is not None
    assert product.current_stock == Decimal("8.000")
    assert len(movements) == 1
    assert movements[0].stock_movement_id == f"mov_sale_{ORDER_ID}_{PRODUCT_ID}"
    assert movements[0].quantity_delta == Decimal("-2.000")
    assert movements[0].reason == "sale"
    assert movements[0].reference_id == ORDER_ID
    assert len(transitions) == 1
    assert transitions[0].from_status == "approved"
    assert transitions[0].to_status == "confirmed"
    assert transitions[0].source == "operator"
    assert transitions[0].occurred_at.replace(tzinfo=timezone.utc) == confirmed_at


def test_confirm_approved_order_duplicate_stock_movement_fails_hard(
    tmp_path: Path,
) -> None:
    storage, lifecycle_store, service = _seed_approved_order(tmp_path)
    storage.append_stock_movement(
        StockMovement(
            tenant_id=DEFAULT_TEST_TENANT_ID,
            stock_movement_id=f"mov_sale_{ORDER_ID}_{PRODUCT_ID}",
            created_at=datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc),
            product_id=PRODUCT_ID,
            quantity_delta=Decimal("-2"),
            reason="sale",
            reference_id=ORDER_ID,
        )
    )

    with pytest.raises(DuplicateStockMovementError):
        service.confirm_approved_order(
            order_id=ORDER_ID,
            tenant_id=DEFAULT_TEST_TENANT_ID,
        )

    order = storage.get_order(ORDER_ID)
    product = storage.get_product(PRODUCT_ID)
    movements = storage.list_stock_movements(product_id=PRODUCT_ID)
    transitions = lifecycle_store.list_order_status_transitions(
        order_id=ORDER_ID,
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )

    assert order is not None
    assert order.status == "approved"
    assert order.confirmed_at is None
    assert product is not None
    assert product.current_stock == Decimal("10.000")
    assert len(movements) == 1
    assert transitions == []


def test_confirm_approved_order_rolls_back_after_stock_movement_insert_failure(
    tmp_path: Path,
) -> None:
    session_factory = _session_factory(tmp_path)
    storage = PostgresStorage(session_factory)
    lifecycle_store = PostgresOrderLifecycleStore(session_factory)
    storage.upsert_product(_product())
    storage.create_order(_order())
    service = OrderService(
        storage,
        atomic_confirmation_store=PostgresAtomicOrderConfirmationStore(
            session_factory,
            fail_after_stock_movements_for_test=True,
        ),
    )

    with pytest.raises(RuntimeError, match="simulated atomic confirmation failure"):
        service.confirm_approved_order(
            order_id=ORDER_ID,
            tenant_id=DEFAULT_TEST_TENANT_ID,
        )

    order = storage.get_order(ORDER_ID)
    product = storage.get_product(PRODUCT_ID)
    movements = storage.list_stock_movements(product_id=PRODUCT_ID)
    transitions = lifecycle_store.list_order_status_transitions(
        order_id=ORDER_ID,
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )

    assert order is not None
    assert order.status == "approved"
    assert order.confirmed_at is None
    assert product is not None
    assert product.current_stock == Decimal("10.000")
    assert movements == []
    assert transitions == []


def test_atomic_confirmation_store_revalidates_status_inside_transaction(
    tmp_path: Path,
) -> None:
    storage, _, _ = _seed_approved_order(tmp_path)
    storage.update_order_status(ORDER_ID, "cancelled")
    confirmation_store = PostgresAtomicOrderConfirmationStore(storage._session_factory)

    with pytest.raises(StorageOrderStatusMismatchError):
        confirmation_store.confirm_order_atomically(
            order_id=ORDER_ID,
            tenant_id=DEFAULT_TEST_TENANT_ID,
            expected_from_status="approved",
            transition_source="operator",
            transition_id="ost_confirm",
            confirmed_at=datetime(2026, 6, 9, 13, 0, tzinfo=timezone.utc),
        )
