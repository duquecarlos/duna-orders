from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from duna_orders.domain.models import (
    Customer,
    Order,
    OrderItem,
    ParseLogEntry,
    Product,
    StockMovement,
)
from duna_orders.storage.base import StorageInterface
from tests.conftest import StorageCase


def make_product(
    run_token: str,
    *,
    product_id: str | None = None,
    product_name: str = "Empanada",
    unit_price: Decimal = Decimal("3000"),
    current_stock: Decimal = Decimal("10"),
    active: bool = True,
    available_days: list[str] | None = None,
) -> Product:
    return Product(
        product_id=product_id or f"{run_token}prd_test",
        product_name=product_name,
        unit_price=unit_price,
        current_stock=current_stock,
        active=active,
        available_days=available_days,
    )


def make_customer(
    run_token: str,
    *,
    customer_id: str | None = None,
    phone: str | None = None,
) -> Customer:
    return Customer(
        customer_id=customer_id or f"{run_token}cus_test",
        customer_name="Cliente Test",
        customer_phone=phone or f"{run_token}3001234567",
    )


def make_order(
    run_token: str,
    *,
    order_id: str | None = None,
    product_id: str | None = None,
    status: str = "draft",
    created_at: datetime | None = None,
) -> Order:
    resolved_order_id = order_id or f"{run_token}ord_test"
    resolved_product_id = product_id or f"{run_token}prd_test"

    item = OrderItem(
        order_item_id=f"{run_token}oit_{resolved_order_id}",
        order_id=resolved_order_id,
        product_id=resolved_product_id,
        product_name_snapshot="Empanada",
        unit_snapshot="unidad",
        quantity=Decimal("2"),
        unit_price_snapshot=Decimal("3000"),
        line_total=Decimal("6000"),
        modifications="sin cebolla",
        validation_status="ok",
    )

    return Order(
        order_id=resolved_order_id,
        created_at=created_at or datetime.now(timezone.utc),
        raw_message="Quiero 2 empanadas",
        status=status,
        items=[item],
        subtotal=Decimal("6000"),
        delivery_fee=Decimal("0"),
        packaging_fee=Decimal("1000"),
        total=Decimal("7000"),
        fulfillment_type="delivery",
        delivery_zone="zona_demo",
        customer_notes="Dejar en portería",
        payment_method="nequi",
    )


def make_stock_movement(
    run_token: str,
    *,
    stock_movement_id: str | None = None,
    product_id: str | None = None,
    quantity_delta: Decimal = Decimal("-2"),
    reason: str = "sale",
) -> StockMovement:
    return StockMovement(
        stock_movement_id=stock_movement_id or f"{run_token}mov_test",
        product_id=product_id or f"{run_token}prd_test",
        quantity_delta=quantity_delta,
        reason=reason,
        reference_id=f"{run_token}ord_test",
    )


def make_parse_log_entry(
    run_token: str,
    *,
    parse_id: str | None = None,
) -> ParseLogEntry:
    return ParseLogEntry(
        parse_id=parse_id or f"{run_token}prs_test",
        raw_message="me regala 2 pollos",
        parsed_json='{"items":[]}',
        model="test-model",
        prompt_version="test-prompt-v1",
        latency_ms=120,
        success=True,
        error=None,
)


def _matching_products(storage: StorageInterface, product_id: str) -> list[Product]:
    return [
        product
        for product in storage.list_products(active_only=False)
        if product.product_id == product_id
    ]


def test_product_upsert_replaces_existing_row_and_list_active_only_default(
    storage_case: StorageCase,
):
    storage = storage_case.storage
    token = storage_case.run_token

    product_id = f"{token}prd_upsert"
    inactive_id = f"{token}prd_inactive"

    storage.upsert_product(
        make_product(
            token,
            product_id=product_id,
            product_name="A",
            unit_price=Decimal("100"),
            active=True,
        )
    )
    storage.upsert_product(
        make_product(
            token,
            product_id=product_id,
            product_name="B",
            unit_price=Decimal("200"),
            active=True,
            available_days=["wednesday", "thursday", "friday"],
        )
    )
    storage.upsert_product(
        make_product(
            token,
            product_id=inactive_id,
            product_name="Inactive",
            active=False,
        )
    )

    matching = _matching_products(storage, product_id)
    active_ids = {
        product.product_id
        for product in storage.list_products()
        if product.product_id.startswith(token)
    }
    all_ids = {
        product.product_id
        for product in storage.list_products(active_only=False)
        if product.product_id.startswith(token)
    }

    assert len(matching) == 1
    assert matching[0].product_name == "B"
    assert matching[0].unit_price == Decimal("200")
    assert matching[0].available_days == ["wednesday", "thursday", "friday"]
    assert product_id in active_ids
    assert inactive_id not in active_ids
    assert inactive_id in all_ids
    assert storage.get_product(f"{token}prd_missing") is None


def test_customer_create_raises_on_duplicate(storage_case: StorageCase):
    storage = storage_case.storage
    token = storage_case.run_token
    customer = make_customer(token)

    storage.create_customer(customer)

    with pytest.raises(ValueError):
        storage.create_customer(customer)


def test_customer_phone_lookup_with_whitespace(storage_case: StorageCase):
    storage = storage_case.storage
    token = storage_case.run_token
    phone = f"{token}3001234567"
    customer = make_customer(token, phone=phone)

    storage.create_customer(customer)

    found_customer = storage.get_customer_by_phone(f" {phone} ")
    missing_customer = storage.get_customer_by_phone(f"{token}0000")

    assert found_customer is not None
    assert found_customer.customer_id == customer.customer_id
    assert missing_customer is None
    assert storage.get_customer(f"{token}cus_missing") is None


def test_create_order_persists_items_and_status_starts_draft(
    storage_case: StorageCase,
):
    storage = storage_case.storage
    token = storage_case.run_token
    order = make_order(token)

    storage.create_order(order)

    saved_order = storage.get_order(order.order_id)

    assert saved_order is not None
    assert saved_order.status == "draft"
    assert len(saved_order.items) == 1
    assert saved_order.items[0].product_name_snapshot == "Empanada"
    assert saved_order.items[0].order_item_id == order.items[0].order_item_id
    assert storage.get_order(f"{token}ord_missing") is None
    assert saved_order.packaging_fee == Decimal("1000")
    assert saved_order.total == Decimal("7000")
    assert saved_order.fulfillment_type == "delivery"
    assert saved_order.delivery_zone == "zona_demo"
    assert saved_order.customer_notes == "Dejar en portería"
    assert saved_order.payment_method == "nequi"
    assert saved_order.items[0].modifications == "sin cebolla"


def test_create_order_raises_on_duplicate(storage_case: StorageCase):
    storage = storage_case.storage
    token = storage_case.run_token
    order = make_order(token)

    storage.create_order(order)

    with pytest.raises(ValueError):
        storage.create_order(order)


def test_update_order_status_confirms_and_sets_timestamp(
    storage_case: StorageCase,
):
    storage = storage_case.storage
    token = storage_case.run_token
    order = make_order(token)
    confirmed_at = datetime.now(timezone.utc).replace(microsecond=123456)

    storage.create_order(order)
    updated_order = storage.update_order_status(
        order.order_id,
        "confirmed",
        confirmed_at=confirmed_at,
    )
    saved_order = storage.get_order(order.order_id)

    assert updated_order.status == "confirmed"
    assert updated_order.confirmed_at == confirmed_at
    assert saved_order is not None
    assert saved_order.confirmed_at == confirmed_at


def test_update_order_status_raises_on_missing_order(storage_case: StorageCase):
    storage = storage_case.storage
    token = storage_case.run_token

    with pytest.raises(KeyError):
        storage.update_order_status(f"{token}ord_missing", "confirmed")


def test_stock_movements_append_only_reversal_nets_to_zero(
    storage_case: StorageCase,
):
    storage = storage_case.storage
    token = storage_case.run_token
    product_id = f"{token}prd_test"

    sale = make_stock_movement(
        token,
        stock_movement_id=f"{token}mov_sale",
        product_id=product_id,
        quantity_delta=Decimal("-2"),
        reason="sale",
    )
    reversal = make_stock_movement(
        token,
        stock_movement_id=f"{token}mov_reversal",
        product_id=product_id,
        quantity_delta=Decimal("2"),
        reason="reversal",
    )

    storage.append_stock_movement(sale)
    storage.append_stock_movement(reversal)

    movements = storage.list_stock_movements(product_id=product_id)
    net_quantity = sum(movement.quantity_delta for movement in movements)

    assert len(movements) == 2
    assert net_quantity == Decimal("0")


def test_append_stock_movement_raises_on_duplicate_id(
    storage_case: StorageCase,
):
    storage = storage_case.storage
    token = storage_case.run_token
    movement = make_stock_movement(token)

    storage.append_stock_movement(movement)

    with pytest.raises(ValueError):
        storage.append_stock_movement(movement)


def test_list_orders_filters_by_status_and_since(storage_case: StorageCase):
    storage = storage_case.storage
    token = storage_case.run_token

    old_date = datetime.now(timezone.utc) - timedelta(days=2)
    new_date = datetime.now(timezone.utc)

    old_draft = make_order(
        token,
        order_id=f"{token}ord_old_draft",
        status="draft",
        created_at=old_date,
    )
    new_draft = make_order(
        token,
        order_id=f"{token}ord_new_draft",
        status="draft",
        created_at=new_date,
    )
    confirmed = make_order(
        token,
        order_id=f"{token}ord_confirmed",
        status="confirmed",
        created_at=new_date,
    )

    storage.create_order(old_draft)
    storage.create_order(new_draft)
    storage.create_order(confirmed)

    draft_ids = {
        order.order_id
        for order in storage.list_orders(status="draft")
        if order.order_id.startswith(token)
    }
    recent_ids = {
        order.order_id
        for order in storage.list_orders(since=new_date - timedelta(minutes=1))
        if order.order_id.startswith(token)
    }

    assert draft_ids == {
        f"{token}ord_old_draft",
        f"{token}ord_new_draft",
    }
    assert recent_ids == {
        f"{token}ord_new_draft",
        f"{token}ord_confirmed",
    }


def test_append_parse_log_persists_entry(storage_case: StorageCase):
    storage = storage_case.storage
    token = storage_case.run_token
    entry = make_parse_log_entry(token)

    saved_entry = storage.append_parse_log(entry)

    assert saved_entry.parse_id == entry.parse_id
    assert saved_entry.parsed_json == entry.parsed_json


def test_append_parse_log_raises_on_duplicate_id(storage_case: StorageCase):
    storage = storage_case.storage
    token = storage_case.run_token
    entry = make_parse_log_entry(token, parse_id=f"{token}prs_fixed_id")

    storage.append_parse_log(entry)

    with pytest.raises(ValueError):
        storage.append_parse_log(entry)