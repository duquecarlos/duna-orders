from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from duna_orders.domain.models import Customer, Order, OrderItem, Product, StockMovement
from duna_orders.storage.memory import InMemoryStorage


def make_product(
    product_id: str = "prd_test",
    product_name: str = "Empanada",
    active: bool = True,
) -> Product:
    return Product(
        product_id=product_id,
        product_name=product_name,
        unit_price=Decimal("3000"),
        current_stock=Decimal("10"),
        active=active,
    )


def make_customer(
    customer_id: str = "cus_test",
    phone: str = "3001234567",
) -> Customer:
    return Customer(
        customer_id=customer_id,
        customer_name="Cliente Test",
        customer_phone=phone,
    )


def make_order(
    order_id: str = "ord_test",
    status: str = "draft",
    created_at: datetime | None = None,
) -> Order:
    item = OrderItem(
        order_item_id=f"oit_{order_id}",
        order_id=order_id,
        product_id="prd_test",
        product_name_snapshot="Empanada",
        unit_snapshot="unidad",
        quantity=Decimal("2"),
        unit_price_snapshot=Decimal("3000"),
        line_total=Decimal("6000"),
        validation_status="ok",
    )

    return Order(
        order_id=order_id,
        created_at=created_at or datetime.now(timezone.utc),
        raw_message="Quiero 2 empanadas",
        status=status,
        items=[item],
        subtotal=Decimal("6000"),
        total=Decimal("6000"),
    )


def make_stock_movement(
    stock_movement_id: str = "mov_test",
    product_id: str = "prd_test",
    quantity_delta: Decimal = Decimal("-2"),
    reason: str = "sale",
) -> StockMovement:
    return StockMovement(
        stock_movement_id=stock_movement_id,
        product_id=product_id,
        quantity_delta=quantity_delta,
        reason=reason,
        reference_id="ord_test",
    )


def test_product_upsert_and_list_active_only_default():
    storage = InMemoryStorage()

    active_product = make_product(product_id="prd_active", active=True)
    inactive_product = make_product(product_id="prd_inactive", active=False)

    storage.upsert_product(active_product)
    storage.upsert_product(inactive_product)

    active_products = storage.list_products()
    all_products = storage.list_products(active_only=False)

    assert len(active_products) == 1
    assert active_products[0].product_id == "prd_active"
    assert len(all_products) == 2


def test_customer_create_raises_on_duplicate():
    storage = InMemoryStorage()
    customer = make_customer()

    storage.create_customer(customer)

    with pytest.raises(ValueError):
        storage.create_customer(customer)


def test_customer_phone_lookup_with_whitespace():
    storage = InMemoryStorage()
    customer = make_customer(phone="3001234567")

    storage.create_customer(customer)

    found_customer = storage.get_customer_by_phone(" 3001234567 ")
    missing_customer = storage.get_customer_by_phone("0000")

    assert found_customer is not None
    assert found_customer.customer_id == customer.customer_id
    assert missing_customer is None


def test_create_order_persists_items_and_status_starts_draft():
    storage = InMemoryStorage()
    order = make_order()

    storage.create_order(order)

    saved_order = storage.get_order(order.order_id)

    assert saved_order is not None
    assert saved_order.status == "draft"
    assert len(saved_order.items) == 1
    assert saved_order.items[0].product_name_snapshot == "Empanada"


def test_create_order_raises_on_duplicate():
    storage = InMemoryStorage()
    order = make_order()

    storage.create_order(order)

    with pytest.raises(ValueError):
        storage.create_order(order)


def test_update_order_status_confirms_and_sets_timestamp():
    storage = InMemoryStorage()
    order = make_order()
    confirmed_at = datetime.now(timezone.utc)

    storage.create_order(order)
    updated_order = storage.update_order_status(
        order.order_id,
        "confirmed",
        confirmed_at=confirmed_at,
    )

    assert updated_order.status == "confirmed"
    assert updated_order.confirmed_at == confirmed_at


def test_update_order_status_raises_on_missing_order():
    storage = InMemoryStorage()

    with pytest.raises(KeyError):
        storage.update_order_status("ord_missing", "confirmed")


def test_stock_movements_append_only_reversal_nets_to_zero():
    storage = InMemoryStorage()

    sale = make_stock_movement(
        stock_movement_id="mov_sale",
        quantity_delta=Decimal("-2"),
        reason="sale",
    )
    reversal = make_stock_movement(
        stock_movement_id="mov_reversal",
        quantity_delta=Decimal("2"),
        reason="reversal",
    )

    storage.append_stock_movement(sale)
    storage.append_stock_movement(reversal)

    movements = storage.list_stock_movements(product_id="prd_test")
    net_quantity = sum(movement.quantity_delta for movement in movements)

    assert len(movements) == 2
    assert net_quantity == Decimal("0")


def test_append_stock_movement_raises_on_duplicate_id():
    storage = InMemoryStorage()
    movement = make_stock_movement()

    storage.append_stock_movement(movement)

    with pytest.raises(ValueError):
        storage.append_stock_movement(movement)


def test_list_orders_filters_by_status_and_since():
    storage = InMemoryStorage()

    old_date = datetime.now(timezone.utc) - timedelta(days=2)
    new_date = datetime.now(timezone.utc)

    old_draft = make_order(
        order_id="ord_old_draft",
        status="draft",
        created_at=old_date,
    )
    new_draft = make_order(
        order_id="ord_new_draft",
        status="draft",
        created_at=new_date,
    )
    confirmed = make_order(
        order_id="ord_confirmed",
        status="confirmed",
        created_at=new_date,
    )

    storage.create_order(old_draft)
    storage.create_order(new_draft)
    storage.create_order(confirmed)

    drafts = storage.list_orders(status="draft")
    recent_orders = storage.list_orders(since=new_date - timedelta(minutes=1))

    assert {order.order_id for order in drafts} == {"ord_old_draft", "ord_new_draft"}
    assert {order.order_id for order in recent_orders} == {
        "ord_new_draft",
        "ord_confirmed",
    }