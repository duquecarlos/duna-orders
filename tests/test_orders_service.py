from datetime import datetime, timezone
from decimal import Decimal
from tests.conftest import DEFAULT_TEST_TENANT_ID
import pytest

from duna_orders.domain.models import (
    Customer,
    DraftItemRequest,
    DraftOrderRequest,
    Order,
    OrderItem,
    OrderStatus,
    OrderStatusTransition,
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
    UnsupportedOrderConfirmationError,
)
from duna_orders.services.orders import (
    OrderService,
    get_allowed_confirmation_statuses,
    get_allowed_next_statuses,
)
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


def test_approved_is_valid_order_status() -> None:
    status: OrderStatus = "approved"
    order = make_order(status=status)

    assert order.status == "approved"


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

def test_create_draft_creates_customer_when_phone_is_new():
    storage = InMemoryStorage()
    storage.upsert_product(make_product())
    service = OrderService(storage)

    order = service.create_draft(
        make_draft_request(
            customer_name="Andrea",
            customer_phone="300 123-4567",
        )
    )

    customers = storage.list_customers()

    assert len(customers) == 1
    assert customers[0].customer_name == "Andrea"
    assert customers[0].customer_phone == "3001234567"
    assert order.customer_id == customers[0].customer_id
    assert order.customer_name_snapshot == "Andrea"
    assert order.customer_phone_snapshot == "3001234567"


def test_create_draft_reuses_existing_customer_by_phone_and_tenant():
    storage = InMemoryStorage()
    storage.upsert_product(make_product())
    storage.create_customer(
        Customer(
            tenant_id=DEFAULT_TEST_TENANT_ID,
            customer_id="cus_andrea",
            customer_name="Andrea Registrada",
            customer_phone="3001234567",
        )
    )
    service = OrderService(storage)

    order = service.create_draft(
        make_draft_request(
            customer_name="Andre",
            customer_phone="300-123-4567",
        )
    )

    customers = storage.list_customers()

    assert len(customers) == 1
    assert order.customer_id == "cus_andrea"
    assert order.customer_name_snapshot == "Andrea Registrada"
    assert order.customer_phone_snapshot == "3001234567"


def test_create_draft_keeps_anonymous_flow_when_phone_is_blank():
    storage = InMemoryStorage()
    storage.upsert_product(make_product())
    service = OrderService(storage)

    order = service.create_draft(
        make_draft_request(
            customer_name="Cliente sin telefono",
            customer_phone="   ",
        )
    )

    assert storage.list_customers() == []
    assert order.customer_id is None
    assert order.customer_name_snapshot == "Cliente sin telefono"
    assert order.customer_phone_snapshot is None

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

def test_confirm_order_repairs_status_when_sale_movement_already_exists():
    storage = InMemoryStorage()
    storage.upsert_product(make_product(current_stock=Decimal("0")))
    storage.create_order(make_order(quantity=Decimal("5")))

    partial_movement = StockMovement(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        stock_movement_id=f"mov_sale_{ORDER_ID}_{PRODUCT_ID}",
        product_id=PRODUCT_ID,
        quantity_delta=Decimal("-5"),
        reason="sale",
        reference_id=ORDER_ID,
    )
    storage.append_stock_movement(partial_movement)

    service = OrderService(storage)

    confirmed_order = service.confirm_order(ORDER_ID)

    movements = storage.list_stock_movements(product_id=PRODUCT_ID)
    product = storage.get_product(PRODUCT_ID)

    assert confirmed_order.status == "confirmed"
    assert len(movements) == 1
    assert product is not None
    assert product.current_stock == Decimal("0")
def test_confirm_order_does_not_repair_when_existing_sale_movement_payload_mismatches():
    storage = InMemoryStorage()
    storage.upsert_product(make_product(current_stock=Decimal("0")))
    storage.create_order(make_order(quantity=Decimal("5")))

    malformed_movement = StockMovement(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        stock_movement_id=f"mov_sale_{ORDER_ID}_{PRODUCT_ID}",
        product_id=PRODUCT_ID,
        quantity_delta=Decimal("-4"),
        reason="sale",
        reference_id=ORDER_ID,
    )
    storage.append_stock_movement(malformed_movement)

    service = OrderService(storage)

    with pytest.raises(InsufficientStockError):
        service.confirm_order(ORDER_ID)

    saved_order = storage.get_order(ORDER_ID)
    movements = storage.list_stock_movements(product_id=PRODUCT_ID)
    product = storage.get_product(PRODUCT_ID)

    assert saved_order is not None
    assert saved_order.status == "draft"
    assert len(movements) == 1
    assert product is not None
    assert product.current_stock == Decimal("0")
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

class FakeLifecycleStore:
    def __init__(self, storage: InMemoryStorage) -> None:
        self.storage = storage
        self.transitions: list[OrderStatusTransition] = []
        self.fail_next_update = False

    def create_order_with_transition(
        self,
        *,
        order: Order,
        transition: OrderStatusTransition,
    ) -> Order:
        created = self.storage.create_order(order)
        self.transitions.append(transition)
        return created

    def update_order_status_with_transition(
        self,
        *,
        order_id: str,
        status: str,
        transition: OrderStatusTransition,
        confirmed_at: datetime | None = None,
        status_updated_at: datetime | None = None,
    ) -> Order:
        if self.fail_next_update:
            self.fail_next_update = False
            raise RuntimeError("simulated lifecycle persistence failure")

        updated = self.storage.update_order_status(
            order_id,
            status,
            confirmed_at=confirmed_at,
            status_updated_at=status_updated_at,
        )
        self.transitions.append(transition)
        return updated

    def list_order_status_transitions(
        self,
        *,
        order_id: str,
        tenant_id: str,
    ) -> list[OrderStatusTransition]:
        return [
            transition
            for transition in self.transitions
            if transition.order_id == order_id and transition.tenant_id == tenant_id
        ]


class GuardedInMemoryStorage(InMemoryStorage):
    def __init__(self) -> None:
        super().__init__()
        self.direct_status_updates = 0
        self.stock_movements_appended = 0
        self.products_upserted = 0

    def update_order_status(
        self,
        order_id: str,
        status: str,
        confirmed_at: datetime | None = None,
        status_updated_at: datetime | None = None,
    ) -> Order:
        self.direct_status_updates += 1
        return super().update_order_status(
            order_id,
            status,
            confirmed_at=confirmed_at,
            status_updated_at=status_updated_at,
        )

    def append_stock_movement(self, movement: StockMovement) -> StockMovement:
        self.stock_movements_appended += 1
        return super().append_stock_movement(movement)

    def upsert_product(self, product: Product) -> Product:
        self.products_upserted += 1
        return super().upsert_product(product)


class GuardrailLifecycleStore(FakeLifecycleStore):
    def __init__(self, storage: GuardedInMemoryStorage) -> None:
        super().__init__(storage)
        self.status_updates: list[str] = []

    def update_order_status_with_transition(
        self,
        *,
        order_id: str,
        status: str,
        transition: OrderStatusTransition,
        confirmed_at: datetime | None = None,
        status_updated_at: datetime | None = None,
    ) -> Order:
        self.status_updates.append(status)
        updated = InMemoryStorage.update_order_status(
            self.storage,
            order_id,
            status,
            confirmed_at=confirmed_at,
            status_updated_at=status_updated_at,
        )
        self.transitions.append(transition)
        return updated


def test_create_draft_with_lifecycle_store_writes_initial_transition() -> None:
    storage = InMemoryStorage()
    storage.upsert_product(make_product())
    lifecycle_store = FakeLifecycleStore(storage)
    service = OrderService(storage, lifecycle_store=lifecycle_store)

    order = service.create_draft(make_draft_request())

    transitions = lifecycle_store.list_order_status_transitions(
        order_id=order.order_id,
        tenant_id=order.tenant_id,
    )

    assert order.status == "draft"
    assert len(transitions) == 1
    assert transitions[0].from_status is None
    assert transitions[0].to_status == "draft"
    assert transitions[0].occurred_at == order.created_at
    assert transitions[0].source == "system"
    assert transitions[0].tenant_id == DEFAULT_TEST_TENANT_ID


def test_review_inbound_draft_approve_moves_draft_to_approved() -> None:
    storage = seed_storage()
    service = OrderService(storage)
    reviewed_at = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)

    order = service.review_inbound_draft(
        order_id=ORDER_ID,
        tenant_id=DEFAULT_TEST_TENANT_ID,
        decision="approve",
        reviewed_at=reviewed_at,
    )

    assert order.status == "approved"
    assert order.status_updated_at == reviewed_at


def test_review_inbound_draft_reject_moves_draft_to_cancelled() -> None:
    storage = seed_storage()
    service = OrderService(storage)
    reviewed_at = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)

    order = service.review_inbound_draft(
        order_id=ORDER_ID,
        tenant_id=DEFAULT_TEST_TENANT_ID,
        decision="reject",
        reviewed_at=reviewed_at,
    )

    assert order.status == "cancelled"
    assert order.status_updated_at == reviewed_at


def test_review_inbound_draft_approve_with_lifecycle_store_appends_transition() -> None:
    storage = seed_storage()
    lifecycle_store = FakeLifecycleStore(storage)
    service = OrderService(storage, lifecycle_store=lifecycle_store)
    reviewed_at = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)

    order = service.review_inbound_draft(
        order_id=ORDER_ID,
        tenant_id=DEFAULT_TEST_TENANT_ID,
        decision="approve",
        reviewed_at=reviewed_at,
    )

    transitions = lifecycle_store.list_order_status_transitions(
        order_id=ORDER_ID,
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )

    assert order.status == "approved"
    assert len(transitions) == 1
    assert transitions[0].from_status == "draft"
    assert transitions[0].to_status == "approved"
    assert transitions[0].occurred_at == reviewed_at
    assert transitions[0].source == "operator"


def test_review_inbound_draft_reject_with_lifecycle_store_appends_transition() -> None:
    storage = seed_storage()
    lifecycle_store = FakeLifecycleStore(storage)
    service = OrderService(storage, lifecycle_store=lifecycle_store)
    reviewed_at = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)

    order = service.review_inbound_draft(
        order_id=ORDER_ID,
        tenant_id=DEFAULT_TEST_TENANT_ID,
        decision="reject",
        reviewed_at=reviewed_at,
    )

    transitions = lifecycle_store.list_order_status_transitions(
        order_id=ORDER_ID,
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )

    assert order.status == "cancelled"
    assert len(transitions) == 1
    assert transitions[0].from_status == "draft"
    assert transitions[0].to_status == "cancelled"
    assert transitions[0].occurred_at == reviewed_at
    assert transitions[0].source == "operator"


def test_review_inbound_draft_refuses_stale_non_draft_order() -> None:
    storage = InMemoryStorage()
    storage.create_order(make_order(status="confirmed"))
    service = OrderService(storage)

    with pytest.raises(InvalidOrderTransitionError) as exc_info:
        service.review_inbound_draft(
            order_id=ORDER_ID,
            tenant_id=DEFAULT_TEST_TENANT_ID,
            decision="approve",
        )

    assert exc_info.value.current_status == "confirmed"
    assert exc_info.value.new_status == "approved"


def test_review_inbound_draft_refuses_tenant_mismatch() -> None:
    storage = seed_storage(order=make_order(tenant_id="tenant_other"))
    service = OrderService(storage)

    with pytest.raises(OrderNotFoundError):
        service.review_inbound_draft(
            order_id=ORDER_ID,
            tenant_id=DEFAULT_TEST_TENANT_ID,
            decision="approve",
        )


def test_review_inbound_draft_rejects_invalid_decision() -> None:
    storage = seed_storage()
    service = OrderService(storage)

    with pytest.raises(ValueError, match="Invalid inbound draft review decision"):
        service.review_inbound_draft(
            order_id=ORDER_ID,
            tenant_id=DEFAULT_TEST_TENANT_ID,
            decision="defer",
        )


def test_review_inbound_draft_approval_does_not_touch_stock_or_products() -> None:
    storage = GuardedInMemoryStorage()
    storage.upsert_product(make_product(current_stock=Decimal("10")))
    storage.products_upserted = 0
    storage.create_order(make_order())
    lifecycle_store = GuardrailLifecycleStore(storage)
    service = OrderService(storage, lifecycle_store=lifecycle_store)

    order = service.review_inbound_draft(
        order_id=ORDER_ID,
        tenant_id=DEFAULT_TEST_TENANT_ID,
        decision="approve",
    )

    product = storage.get_product(PRODUCT_ID)

    assert order.status == "approved"
    assert storage.stock_movements_appended == 0
    assert storage.products_upserted == 0
    assert product is not None
    assert product.current_stock == Decimal("10")
    assert lifecycle_store.status_updates == ["approved"]


def test_confirm_order_with_lifecycle_store_appends_transition() -> None:
    storage = seed_storage()
    lifecycle_store = FakeLifecycleStore(storage)
    service = OrderService(storage, lifecycle_store=lifecycle_store)
    confirmed_at = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)

    order = service.confirm_order(ORDER_ID, confirmed_at=confirmed_at)

    transitions = lifecycle_store.list_order_status_transitions(
        order_id=ORDER_ID,
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )

    assert order.status == "confirmed"
    assert len(transitions) == 1
    assert transitions[0].from_status == "draft"
    assert transitions[0].to_status == "confirmed"
    assert transitions[0].occurred_at == confirmed_at
    assert transitions[0].source == "operator"
    assert transitions[0].tenant_id == DEFAULT_TEST_TENANT_ID


def test_transition_order_status_with_lifecycle_store_appends_transition() -> None:
    storage = InMemoryStorage()
    storage.create_order(make_order(status="confirmed", fulfillment_type="delivery"))
    lifecycle_store = FakeLifecycleStore(storage)
    service = OrderService(storage, lifecycle_store=lifecycle_store)
    changed_at = datetime(2026, 6, 7, 12, 5, tzinfo=timezone.utc)

    order = service.transition_order_status(
        ORDER_ID,
        DEFAULT_TEST_TENANT_ID,
        "in_preparation",
        status_updated_at=changed_at,
    )

    transitions = lifecycle_store.list_order_status_transitions(
        order_id=ORDER_ID,
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )

    assert order.status == "in_preparation"
    assert len(transitions) == 1
    assert transitions[0].from_status == "confirmed"
    assert transitions[0].to_status == "in_preparation"
    assert transitions[0].occurred_at == changed_at
    assert transitions[0].source == "operator"


def test_rejected_transition_with_lifecycle_store_writes_no_transition() -> None:
    storage = InMemoryStorage()
    storage.create_order(make_order(status="confirmed", fulfillment_type="delivery"))
    lifecycle_store = FakeLifecycleStore(storage)
    service = OrderService(storage, lifecycle_store=lifecycle_store)

    with pytest.raises(InvalidOrderTransitionError):
        service.transition_order_status(
            ORDER_ID,
            DEFAULT_TEST_TENANT_ID,
            "delivered",
        )

    transitions = lifecycle_store.list_order_status_transitions(
        order_id=ORDER_ID,
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )

    assert transitions == []


def test_lifecycle_store_failure_prevents_status_update() -> None:
    storage = InMemoryStorage()
    storage.create_order(make_order(status="confirmed", fulfillment_type="delivery"))
    lifecycle_store = FakeLifecycleStore(storage)
    lifecycle_store.fail_next_update = True
    service = OrderService(storage, lifecycle_store=lifecycle_store)

    with pytest.raises(RuntimeError, match="simulated lifecycle persistence failure"):
        service.transition_order_status(
            ORDER_ID,
            DEFAULT_TEST_TENANT_ID,
            "in_preparation",
        )

    saved_order = storage.get_order(ORDER_ID)

    assert saved_order is not None
    assert saved_order.status == "confirmed"
    assert lifecycle_store.transitions == []


def test_lifecycle_store_injection_avoids_direct_storage_status_updates() -> None:
    storage = GuardedInMemoryStorage()
    storage.upsert_product(make_product())
    storage.create_order(make_order(status="draft", fulfillment_type="delivery"))
    lifecycle_store = GuardrailLifecycleStore(storage)
    service = OrderService(storage, lifecycle_store=lifecycle_store)

    confirmed_order = service.confirm_order(ORDER_ID)
    transitioned_order = service.transition_order_status(
        ORDER_ID,
        DEFAULT_TEST_TENANT_ID,
        "in_preparation",
    )

    assert confirmed_order.status == "confirmed"
    assert transitioned_order.status == "in_preparation"
    assert lifecycle_store.status_updates == ["confirmed", "in_preparation"]
    assert [transition.to_status for transition in lifecycle_store.transitions] == [
        "confirmed",
        "in_preparation",
    ]
    assert storage.direct_status_updates == 0


def test_review_inbound_draft_lifecycle_store_injection_avoids_direct_storage_status_updates() -> None:
    storage = GuardedInMemoryStorage()
    storage.create_order(make_order(status="draft", fulfillment_type="delivery"))
    lifecycle_store = GuardrailLifecycleStore(storage)
    service = OrderService(storage, lifecycle_store=lifecycle_store)

    approved_order = service.review_inbound_draft(
        order_id=ORDER_ID,
        tenant_id=DEFAULT_TEST_TENANT_ID,
        decision="approve",
    )

    assert approved_order.status == "approved"
    assert lifecycle_store.status_updates == ["approved"]
    assert [transition.to_status for transition in lifecycle_store.transitions] == [
        "approved",
    ]
    assert storage.direct_status_updates == 0


class FakeAtomicConfirmationStore:
    def __init__(self, storage: InMemoryStorage) -> None:
        self.storage = storage
        self.calls: list[dict[str, object]] = []

    def confirm_order_atomically(
        self,
        *,
        order_id: str,
        tenant_id: str,
        expected_from_status: str,
        transition_source: str,
        transition_id: str,
        confirmed_at: datetime,
    ) -> Order:
        self.calls.append(
            {
                "order_id": order_id,
                "tenant_id": tenant_id,
                "expected_from_status": expected_from_status,
                "transition_source": transition_source,
                "transition_id": transition_id,
                "confirmed_at": confirmed_at,
            }
        )
        return self.storage.update_order_status(
            order_id,
            "confirmed",
            confirmed_at=confirmed_at,
        )


class ConfirmOrderForbiddenService(OrderService):
    def confirm_order(
        self,
        order_id: str,
        confirmed_at: datetime | None = None,
    ) -> Order:
        raise AssertionError("confirm_order must not be called")


def test_confirm_approved_order_uses_atomic_store_and_confirms_order() -> None:
    storage = seed_storage(order=make_order(status="approved"))
    atomic_store = FakeAtomicConfirmationStore(storage)
    service = OrderService(storage, atomic_confirmation_store=atomic_store)
    confirmed_at = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)

    order = service.confirm_approved_order(
        order_id=ORDER_ID,
        tenant_id=DEFAULT_TEST_TENANT_ID,
        confirmed_at=confirmed_at,
    )

    assert order.status == "confirmed"
    assert order.confirmed_at == confirmed_at
    assert len(atomic_store.calls) == 1
    assert atomic_store.calls[0]["expected_from_status"] == "approved"
    assert atomic_store.calls[0]["transition_source"] == "operator"


def test_confirm_approved_order_does_not_call_legacy_confirm_order() -> None:
    storage = seed_storage(order=make_order(status="approved"))
    atomic_store = FakeAtomicConfirmationStore(storage)
    service = ConfirmOrderForbiddenService(
        storage,
        atomic_confirmation_store=atomic_store,
    )

    order = service.confirm_approved_order(
        order_id=ORDER_ID,
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )

    assert order.status == "confirmed"
    assert len(atomic_store.calls) == 1


def test_confirm_approved_order_requires_atomic_capability() -> None:
    storage = seed_storage(order=make_order(status="approved"))
    service = OrderService(storage)

    with pytest.raises(UnsupportedOrderConfirmationError):
        service.confirm_approved_order(
            order_id=ORDER_ID,
            tenant_id=DEFAULT_TEST_TENANT_ID,
        )


@pytest.mark.parametrize("status", ["draft", "confirmed", "cancelled"])
def test_confirm_approved_order_refuses_non_approved_status(status: str) -> None:
    storage = seed_storage(order=make_order(status=status))
    atomic_store = FakeAtomicConfirmationStore(storage)
    service = OrderService(storage, atomic_confirmation_store=atomic_store)

    with pytest.raises(InvalidOrderTransitionError) as exc_info:
        service.confirm_approved_order(
            order_id=ORDER_ID,
            tenant_id=DEFAULT_TEST_TENANT_ID,
        )

    assert exc_info.value.current_status == status
    assert exc_info.value.new_status == "confirmed"
    assert atomic_store.calls == []


def test_confirm_approved_order_refuses_tenant_mismatch() -> None:
    storage = seed_storage(order=make_order(status="approved", tenant_id="tenant_other"))
    atomic_store = FakeAtomicConfirmationStore(storage)
    service = OrderService(storage, atomic_confirmation_store=atomic_store)

    with pytest.raises(OrderNotFoundError):
        service.confirm_approved_order(
            order_id=ORDER_ID,
            tenant_id=DEFAULT_TEST_TENANT_ID,
        )

    assert atomic_store.calls == []


def test_approved_to_confirmed_is_not_generic_status_transition() -> None:
    order = make_order(status="approved")

    assert get_allowed_next_statuses(order) == ()
    assert "in_preparation" not in get_allowed_next_statuses(order)


def test_approved_to_confirmed_is_confirmation_transition() -> None:
    order = make_order(status="approved")

    assert get_allowed_confirmation_statuses(order) == ("confirmed",)


def test_transition_order_status_rejects_approved_to_confirmed() -> None:
    storage = InMemoryStorage()
    storage.create_order(make_order(status="approved"))
    service = OrderService(storage)

    with pytest.raises(InvalidOrderTransitionError) as exc_info:
        service.transition_order_status(
            ORDER_ID,
            DEFAULT_TEST_TENANT_ID,
            "confirmed",
        )

    assert exc_info.value.current_status == "approved"
    assert exc_info.value.new_status == "confirmed"
    order = storage.get_order(ORDER_ID)
    assert order is not None
    assert order.status == "approved"
    assert order.confirmed_at is None
