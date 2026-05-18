from datetime import datetime, timezone
from decimal import Decimal

import pytest

from duna_orders.domain.models import Order, OrderItem, Product, StockMovement
from duna_orders.services.exceptions import (
    InsufficientStockError,
    InvalidOrderStateError,
    OrderNotFoundError,
    ProductNotFoundError,
)
from duna_orders.services.orders import OrderService
from duna_orders.storage.memory import InMemoryStorage


PRODUCT_ID = "prd_empanada"
ORDER_ID = "ord_test"


def make_product(
    product_id: str = PRODUCT_ID,
    current_stock: Decimal = Decimal("10"),
) -> Product:
    return Product(
        product_id=product_id,
        product_name="Empanada",
        unit_price=Decimal("3000"),
        current_stock=current_stock,
    )


def make_order(
    order_id: str = ORDER_ID,
    product_id: str = PRODUCT_ID,
    quantity: Decimal = Decimal("2"),
    status: str = "draft",
) -> Order:
    item = OrderItem(
        order_item_id="oit_test",
        order_id=order_id,
        product_id=product_id,
        product_name_snapshot="Empanada",
        unit_snapshot="unidad",
        quantity=quantity,
        unit_price_snapshot=Decimal("3000"),
        line_total=quantity * Decimal("3000"),
        validation_status="ok",
    )

    return Order(
        order_id=order_id,
        raw_message="Quiero 2 empanadas",
        status=status,
        items=[item],
        subtotal=quantity * Decimal("3000"),
        total=quantity * Decimal("3000"),
    )


def seed_storage(
    *,
    product: Product | None = None,
    order: Order | None = None,
) -> InMemoryStorage:
    storage = InMemoryStorage()
    storage.upsert_product(product or make_product())
    storage.create_order(order or make_order())
    return storage


def test_confirm_order_happy_path():
    storage = seed_storage()
    service = OrderService(storage)

    confirmed_order = service.confirm_order(ORDER_ID)

    movements = storage.list_stock_movements()
    product = storage.get_product(PRODUCT_ID)

    assert confirmed_order.status == "confirmed"
    assert confirmed_order.confirmed_at is not None
    assert len(movements) == 1
    assert movements[0].quantity_delta == Decimal("-2")
    assert movements[0].reason == "sale"
    assert movements[0].related_order_id == ORDER_ID
    assert product is not None
    assert product.current_stock == Decimal("8")


def test_confirm_order_raises_when_order_missing():
    storage = InMemoryStorage()
    service = OrderService(storage)

    with pytest.raises(OrderNotFoundError):
        service.confirm_order("ord_missing")


def test_confirm_order_raises_when_not_draft():
    storage = seed_storage()
    service = OrderService(storage)

    service.confirm_order(ORDER_ID)

    with pytest.raises(InvalidOrderStateError) as exc_info:
        service.confirm_order(ORDER_ID)

    assert exc_info.value.status == "confirmed"


def test_confirm_order_raises_on_insufficient_stock():
    storage = seed_storage(product=make_product(current_stock=Decimal("1")))
    service = OrderService(storage)

    with pytest.raises(InsufficientStockError):
        service.confirm_order(ORDER_ID)

    order = storage.get_order(ORDER_ID)

    assert order is not None
    assert order.status == "draft"
    assert storage.list_stock_movements() == []


def test_confirm_order_raises_when_product_missing():
    storage = InMemoryStorage()
    storage.create_order(make_order(product_id="prd_missing"))

    service = OrderService(storage)

    with pytest.raises(ProductNotFoundError):
        service.confirm_order(ORDER_ID)


def test_confirm_order_is_idempotent_on_retry():
    storage = seed_storage()

    partial_movement = StockMovement(
        stock_movement_id=f"mov_sale_{ORDER_ID}_{PRODUCT_ID}",
        product_id=PRODUCT_ID,
        quantity_delta=Decimal("-2"),
        reason="sale",
        related_order_id=ORDER_ID,
    )

    storage.append_stock_movement(partial_movement)
    storage.upsert_product(make_product(current_stock=Decimal("8")))

    service = OrderService(storage)
    confirmed_order = service.confirm_order(ORDER_ID)

    movements = storage.list_stock_movements(product_id=PRODUCT_ID)
    product = storage.get_product(PRODUCT_ID)

    assert confirmed_order.status == "confirmed"
    assert len(movements) == 1
    assert product is not None
    assert product.current_stock == Decimal("8")