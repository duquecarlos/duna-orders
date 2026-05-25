from datetime import datetime, timezone
from decimal import Decimal

from duna_orders.domain.models import Customer, Order
from duna_orders.services.customer_context import (
    format_new_order_customer_context,
    format_today_order_customer_badge,
    get_customer_context_by_phone,
)
from duna_orders.storage.memory import InMemoryStorage
from tests.conftest import DEFAULT_TEST_TENANT_ID


def make_customer() -> Customer:
    return Customer(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        customer_id="cus_andrea",
        customer_name="Andrea",
        customer_phone="3001234567",
    )


def make_order(order_id: str, created_at: datetime) -> Order:
    return Order(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        order_id=order_id,
        customer_id="cus_andrea",
        raw_message="Pedido de prueba",
        created_at=created_at,
        subtotal=Decimal("10000"),
        total=Decimal("10000"),
    )


def test_get_customer_context_returns_empty_context_for_blank_phone():
    storage = InMemoryStorage()

    context = get_customer_context_by_phone(
        storage,
        tenant_id=DEFAULT_TEST_TENANT_ID,
        phone="   ",
    )

    assert context.customer is None
    assert context.previous_orders == []
    assert format_new_order_customer_context(context) == "Cliente nuevo"
    assert format_today_order_customer_badge(context) == "First order"


def test_get_customer_context_returns_known_customer_and_history():
    storage = InMemoryStorage()
    storage.create_customer(make_customer())
    storage.create_order(
        make_order(
            "ord_old",
            datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
        )
    )
    storage.create_order(
        make_order(
            "ord_new",
            datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc),
        )
    )

    context = get_customer_context_by_phone(
        storage,
        tenant_id=DEFAULT_TEST_TENANT_ID,
        phone="300 123-4567",
    )

    assert context.customer is not None
    assert context.customer.customer_name == "Andrea"
    assert [order.order_id for order in context.previous_orders] == [
        "ord_new",
        "ord_old",
    ]
    assert format_new_order_customer_context(context) == (
        "Cliente conocido: Andrea - 2 pedidos anteriores"
    )
    assert format_today_order_customer_badge(context) == "Repeat customer (2 orders)"


def test_customer_context_formats_single_previous_order():
    storage = InMemoryStorage()
    storage.create_customer(make_customer())
    storage.create_order(
        make_order(
            "ord_one",
            datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
        )
    )

    context = get_customer_context_by_phone(
        storage,
        tenant_id=DEFAULT_TEST_TENANT_ID,
        phone="3001234567",
    )

    assert format_new_order_customer_context(context) == (
        "Cliente conocido: Andrea - 1 pedido anterior"
    )
    assert format_today_order_customer_badge(context) == "First order"