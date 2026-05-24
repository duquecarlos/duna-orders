from datetime import datetime, timezone
from decimal import Decimal
from tests.conftest import DEFAULT_TEST_TENANT_ID
import pytest

from duna_orders.domain.models import (
    DraftItemRequest,
    DraftOrderRequest,
    Order,
    OrderItem,
    Product,
    StockMovement,
)
from duna_orders.services.exceptions import (
    EmptyDraftError,
    InactiveProductError,
    InsufficientStockError,
    InvalidOrderStateError,
    InvalidOrderTransitionError,
    OrderNotFoundError,
    ProductNotFoundError,
)
from duna_orders.services.orders import OrderService
from duna_orders.storage.memory import InMemoryStorage


PRODUCT_ID = "prd_empanada"
ORDER_ID = "ord_test"


def make_product(
    product_id: str = PRODUCT_ID,
    product_name: str = "Empanada",
    unit_price: Decimal = Decimal("3000"),
    current_stock: Decimal = Decimal("10"),
    active: bool = True,
) -> Product:
    return Product(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        product_id=product_id,
        product_name=product_name,
        unit_price=unit_price,
        current_stock=current_stock,
        active=active,
    )


def make_order(
    order_id: str = ORDER_ID,
    product_id: str = PRODUCT_ID,
    quantity: Decimal = Decimal("2"),
    status: str = "draft",
    tenant_id: str = DEFAULT_TEST_TENANT_ID,
    fulfillment_type: str | None = None,
    ) -> Order:
    item = OrderItem(
        tenant_id=tenant_id,
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
        tenant_id=tenant_id,
        order_id=order_id,
        raw_message="Quiero 2 empanadas",
        status=status,
        items=[item],
        subtotal=quantity * Decimal("3000"),
        total=quantity * Decimal("3000"),
        fulfillment_type=fulfillment_type,
    )


def make_draft_request(
    items: list[DraftItemRequest] | None = None,
    customer_name: str = "Cliente Test",
    customer_phone: str | None = None,
    raw_message: str = "Buenas, quiero hacer un pedido",
    fulfillment_type: str | None = None,
    delivery_zone: str | None = None,
    packaging_fee: Decimal = Decimal("0"),
    customer_notes: str | None = None,
    payment_method: str | None = None,
    modifications: str | None = None,
) -> DraftOrderRequest:
    if items is None:
        items = [
            DraftItemRequest(
                tenant_id=DEFAULT_TEST_TENANT_ID,
                product_id=PRODUCT_ID,
                quantity=Decimal("2"),
                modifications=modifications,
            ),
        ]

    return DraftOrderRequest(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        raw_message=raw_message,
        customer_name=customer_name,
        customer_phone=customer_phone,
        items=items,
        fulfillment_type=fulfillment_type,
        delivery_zone=delivery_zone,
        packaging_fee=packaging_fee,
        customer_notes=customer_notes,
        payment_method=payment_method,
    )


def seed_storage(
    *,
    product: Product | None = None,
    order: Order | None = None,
) -> InMemoryStorage:
    storage = InMemoryStorage()
    storage.upsert_product(product or make_product())

    if order is not None:
        storage.create_order(order)
    else:
        storage.create_order(make_order())

    return storage


def test_create_draft_happy_path():
    storage = InMemoryStorage()
    storage.upsert_product(
        make_product(
            product_id="prd_pollo",
            product_name="Pollo entero",
            unit_price=Decimal("25000"),
        )
    )
    storage.upsert_product(
        make_product(
            product_id="prd_gaseosa",
            product_name="Gaseosa 1.5L",
            unit_price=Decimal("6500"),
        )
    )

    service = OrderService(storage)
    request = DraftOrderRequest(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        raw_message="Buenas, me regala 2 pollos y 3 gaseosas",
        customer_name="Cliente Test",
        items=[
            DraftItemRequest(
                tenant_id=DEFAULT_TEST_TENANT_ID,
                product_id="prd_pollo",
                quantity=Decimal("2"),
                modifications="sin cebolla",
            ),
            DraftItemRequest(
                tenant_id=DEFAULT_TEST_TENANT_ID,
                product_id="prd_gaseosa",
                quantity=Decimal("3"),
            ),
        ],
        fulfillment_type="delivery",
        delivery_zone="zona_demo",
        packaging_fee=Decimal("1000"),
        customer_notes="Sin cubiertos",
        payment_method="nequi",
    )

    order = service.create_draft(request)

    assert order.order_id.startswith("ord_")
    assert order.status == "draft"
    assert len(order.items) == 2
    assert order.subtotal == Decimal("69500")
    assert order.delivery_fee == Decimal("0")
    assert order.packaging_fee == Decimal("1000")
    assert order.total == Decimal("70500")
    assert order.fulfillment_type == "delivery"
    assert order.delivery_zone == "zona_demo"
    assert order.customer_notes == "Sin cubiertos"
    assert order.payment_method == "nequi"
    assert order.items[0].modifications == "sin cebolla"


def test_create_draft_raises_on_empty_items():
    storage = InMemoryStorage()
    service = OrderService(storage)

    request = make_draft_request(items=[])

    with pytest.raises(EmptyDraftError):
        service.create_draft(request)


def test_create_draft_raises_when_all_quantities_zero():
    storage = InMemoryStorage()
    storage.upsert_product(make_product())
    service = OrderService(storage)

    request = make_draft_request(
        items=[
            DraftItemRequest(
                tenant_id=DEFAULT_TEST_TENANT_ID,
                product_id=PRODUCT_ID,
                quantity=Decimal("0"),
            ),
                    ]
    )

    with pytest.raises(EmptyDraftError):
        service.create_draft(request)


def test_create_draft_raises_on_unknown_product():
    storage = InMemoryStorage()
    service = OrderService(storage)

    request = make_draft_request(
        items=[
            DraftItemRequest(
                    tenant_id=DEFAULT_TEST_TENANT_ID,
                    product_id="prd_does_not_exist",
                    quantity=Decimal("1"),
                ),
        ]
    )

    with pytest.raises(ProductNotFoundError):
        service.create_draft(request)


def test_create_draft_raises_on_inactive_product():
    storage = InMemoryStorage()
    storage.upsert_product(make_product(active=False))
    service = OrderService(storage)

    request = make_draft_request()

    with pytest.raises(InactiveProductError):
        service.create_draft(request)


def test_create_draft_snapshots_are_immutable():
    storage = InMemoryStorage()
    storage.upsert_product(
        make_product(
            product_name="Pollo entero",
            unit_price=Decimal("25000"),
        )
    )

    service = OrderService(storage)
    order = service.create_draft(make_draft_request())

    updated_product = make_product(
        product_name="Pollo grande",
        unit_price=Decimal("30000"),
    )
    storage.upsert_product(updated_product)

    saved_order = storage.get_order(order.order_id)

    assert saved_order is not None
    assert saved_order.items[0].product_name_snapshot == "Pollo entero"
    assert saved_order.items[0].unit_price_snapshot == Decimal("25000")


def test_create_draft_computes_subtotal_and_total_correctly():
    storage = InMemoryStorage()
    storage.upsert_product(
        make_product(
            product_id="prd_pollo",
            product_name="Pollo entero",
            unit_price=Decimal("25000"),
        )
    )
    storage.upsert_product(
        make_product(
            product_id="prd_gaseosa",
            product_name="Gaseosa 1.5L",
            unit_price=Decimal("6500"),
        )
    )

    service = OrderService(storage)
    request = DraftOrderRequest(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        raw_message="Buenas, me regala 2 pollos y 3 gaseosas",
        customer_name="Cliente Test",
        items=[
        DraftItemRequest(
            tenant_id=DEFAULT_TEST_TENANT_ID,
            product_id="prd_pollo",
            quantity=Decimal("2"),
        ),
        DraftItemRequest(
            tenant_id=DEFAULT_TEST_TENANT_ID,
            product_id="prd_gaseosa",
            quantity=Decimal("3"),
        ),
        ],
    )

    order = service.create_draft(request)

    assert order.subtotal == Decimal("69500")
    assert order.delivery_fee == Decimal("0")
    assert order.total == Decimal("69500")


def test_confirm_order_happy_path():
    storage = seed_storage()
    service = OrderService(storage)

    confirmed_order = service.confirm_order(ORDER_ID)

    movements = storage.list_stock_movements()
    product = storage.get_product(PRODUCT_ID)

    assert confirmed_order.tenant_id == DEFAULT_TEST_TENANT_ID
    assert movements[0].tenant_id == DEFAULT_TEST_TENANT_ID
    assert all(item.tenant_id == DEFAULT_TEST_TENANT_ID for item in confirmed_order.items)
    assert confirmed_order.status == "confirmed"
    assert confirmed_order.confirmed_at is not None
    assert len(movements) == 1
    assert movements[0].quantity_delta == Decimal("-2")
    assert movements[0].reason == "sale"
    assert movements[0].reference_id == ORDER_ID
    assert product is not None
    assert product.current_stock == Decimal("8")

def test_confirm_order_aggregates_stock_impact_for_duplicate_product_items():
    storage = InMemoryStorage()
    storage.upsert_product(make_product(current_stock=Decimal("10")))
    service = OrderService(storage)

    request = make_draft_request(
        items=[
            DraftItemRequest(
                tenant_id=DEFAULT_TEST_TENANT_ID,
                product_id=PRODUCT_ID,
                quantity=Decimal("1"),
                modifications="sin chicharrón",
            ),
            DraftItemRequest(
                tenant_id=DEFAULT_TEST_TENANT_ID,
                product_id=PRODUCT_ID,
                quantity=Decimal("1"),
                modifications="con extra aguacate",
            ),
        ]
    )

    order = service.create_draft(request)
    confirmed_order = service.confirm_order(order.order_id)

    movements = storage.list_stock_movements(product_id=PRODUCT_ID)
    product = storage.get_product(PRODUCT_ID)

    assert confirmed_order.status == "confirmed"
    assert len(order.items) == 2
    assert len(movements) == 1
    assert movements[0].quantity_delta == Decimal("-2")
    assert movements[0].reference_id == order.order_id
    assert product is not None
    assert product.current_stock == Decimal("8")


def test_confirm_order_raises_on_insufficient_aggregate_stock_for_duplicate_product_items():
    storage = InMemoryStorage()
    storage.upsert_product(make_product(current_stock=Decimal("1")))
    service = OrderService(storage)

    request = make_draft_request(
        items=[
            DraftItemRequest(
                tenant_id=DEFAULT_TEST_TENANT_ID,
                product_id=PRODUCT_ID,
                quantity=Decimal("1"),
            ),
            DraftItemRequest(
                tenant_id=DEFAULT_TEST_TENANT_ID,
                product_id=PRODUCT_ID,
                quantity=Decimal("1"),
            ),
        ]
    )

    order = service.create_draft(request)

    with pytest.raises(InsufficientStockError):
        service.confirm_order(order.order_id)

    saved_order = storage.get_order(order.order_id)
    product = storage.get_product(PRODUCT_ID)

    assert saved_order is not None
    assert saved_order.status == "draft"
    assert storage.list_stock_movements() == []
    assert product is not None
    assert product.current_stock == Decimal("1")

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
        tenant_id=DEFAULT_TEST_TENANT_ID,
        stock_movement_id=f"mov_sale_{ORDER_ID}_{PRODUCT_ID}",
        product_id=PRODUCT_ID,
        quantity_delta=Decimal("-2"),
        reason="sale",
        reference_id=ORDER_ID,
    )

    storage.append_stock_movement(partial_movement)
    storage.upsert_product(make_product(current_stock=Decimal("8")))

    service = OrderService(storage)
    confirmed_order = service.confirm_order(ORDER_ID)

    movements = storage.list_stock_movements(product_id=PRODUCT_ID)
    product = storage.get_product(PRODUCT_ID)

    assert confirmed_order.tenant_id == DEFAULT_TEST_TENANT_ID
    assert movements[0].tenant_id == DEFAULT_TEST_TENANT_ID
    assert confirmed_order.status == "confirmed"
    assert len(movements) == 1
    assert product is not None
    assert product.current_stock == Decimal("8")

def test_transition_order_status_confirmed_to_in_preparation():
    storage = InMemoryStorage()
    storage.create_order(make_order(status="confirmed", fulfillment_type="delivery"))
    service = OrderService(storage)

    changed_at = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)

    order = service.transition_order_status(
        ORDER_ID,
        DEFAULT_TEST_TENANT_ID,
        "in_preparation",
        status_updated_at=changed_at,
    )

    assert order.status == "in_preparation"
    assert order.status_updated_at == changed_at


def test_transition_order_status_ready_to_delivered_for_delivery():
    storage = InMemoryStorage()
    storage.create_order(make_order(status="ready", fulfillment_type="delivery"))
    service = OrderService(storage)

    order = service.transition_order_status(
        ORDER_ID,
        DEFAULT_TEST_TENANT_ID,
        "delivered",
    )

    assert order.status == "delivered"


def test_transition_order_status_ready_to_picked_up_for_pickup():
    storage = InMemoryStorage()
    storage.create_order(make_order(status="ready", fulfillment_type="pickup"))
    service = OrderService(storage)

    order = service.transition_order_status(
        ORDER_ID,
        DEFAULT_TEST_TENANT_ID,
        "picked_up",
    )

    assert order.status == "picked_up"


def test_transition_order_status_rejects_direct_confirmed_to_delivered():
    storage = InMemoryStorage()
    storage.create_order(make_order(status="confirmed", fulfillment_type="delivery"))
    service = OrderService(storage)

    with pytest.raises(InvalidOrderTransitionError) as exc_info:
        service.transition_order_status(
            ORDER_ID,
            DEFAULT_TEST_TENANT_ID,
            "delivered",
        )

    assert exc_info.value.current_status == "confirmed"
    assert exc_info.value.new_status == "delivered"


def test_transition_order_status_rejects_terminal_state_transition():
    storage = InMemoryStorage()
    storage.create_order(make_order(status="delivered", fulfillment_type="delivery"))
    service = OrderService(storage)

    with pytest.raises(InvalidOrderTransitionError):
        service.transition_order_status(
            ORDER_ID,
            DEFAULT_TEST_TENANT_ID,
            "cancelled",
        )


def test_transition_order_status_respects_tenant_scope():
    storage = InMemoryStorage()
    storage.create_order(
        make_order(
            status="confirmed",
            tenant_id="tenant_other",
            fulfillment_type="delivery",
        )
    )
    service = OrderService(storage)

    with pytest.raises(OrderNotFoundError):
        service.transition_order_status(
            ORDER_ID,
            DEFAULT_TEST_TENANT_ID,
            "in_preparation",
        )