from datetime import datetime
from decimal import Decimal
import logging
from typing import Literal, Protocol
from duna_orders.domain.models import (
    Customer,
    DraftOrderRequest,
    Order,
    OrderItem,
    StockMovement,
    utc_now,
    OrderStatusTransition,
)
from duna_orders.storage.order_lifecycle import OrderLifecycleStore
from duna_orders.domain.phone import normalize_customer_phone
from duna_orders.ids import new_id
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
from duna_orders.storage.base import StorageInterface
from duna_orders.storage.exceptions import (
    StorageInsufficientStockError,
    StorageOrderStatusMismatchError,
    StorageProductNotFoundError,
)

BASE_STATUS_TRANSITIONS = {
    "draft": ("approved", "cancelled"),
    "confirmed": ("in_preparation", "cancelled"),
    "in_preparation": ("ready", "cancelled"),
}
logger = logging.getLogger(__name__)


CONFIRMATION_STATUS_TRANSITIONS = {
    "approved": ("confirmed",),
}


def get_allowed_next_statuses(order: Order) -> tuple[str, ...]:
    if order.status == "ready":
        if order.fulfillment_type == "delivery":
            return ("delivered", "cancelled")

        if order.fulfillment_type == "pickup":
            return ("picked_up", "cancelled")

        return ("cancelled",)

    return BASE_STATUS_TRANSITIONS.get(order.status, ())


def get_allowed_confirmation_statuses(order: Order) -> tuple[str, ...]:
    return CONFIRMATION_STATUS_TRANSITIONS.get(order.status, ())

class AtomicOrderConfirmationStore(Protocol):
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
        ...


class OrderService:
    def __init__(
        self,
        storage: StorageInterface,
        lifecycle_store: OrderLifecycleStore | None = None,
        atomic_confirmation_store: AtomicOrderConfirmationStore | None = None,
    ) -> None:
        self._storage = storage
        self._lifecycle_store = lifecycle_store
        self._atomic_confirmation_store = atomic_confirmation_store
    def create_draft(self, request: DraftOrderRequest) -> Order:
        positive_items = [item for item in request.items if item.quantity > 0]

        if not positive_items:
            raise EmptyDraftError()

        order_id = new_id("ord")
        order_items: list[OrderItem] = []

        for item_request in positive_items:
            product = self._storage.get_product(item_request.product_id)

            if product is None:
                raise ProductNotFoundError(item_request.product_id)

            if not product.active:
                raise InactiveProductError(item_request.product_id)

            line_total = item_request.quantity * product.unit_price

            order_items.append(
                OrderItem(
                    tenant_id=request.tenant_id,
                    order_item_id=new_id("oit"),
                    order_id=order_id,
                    product_id=product.product_id,
                    product_name_snapshot=product.product_name,
                    unit_snapshot=product.unit,
                    quantity=item_request.quantity,
                    unit_price_snapshot=product.unit_price,
                    line_total=line_total,
                    modifications=item_request.modifications,
                    validation_status="ok",
                )
            )

        subtotal = sum((item.line_total for item in order_items), Decimal("0"))
        delivery_fee = Decimal("0")
        packaging_fee = request.packaging_fee
        total = subtotal + delivery_fee + packaging_fee
        customer_id = None
        customer_name_snapshot = request.customer_name
        customer_phone_snapshot = normalize_customer_phone(request.customer_phone)

        if customer_phone_snapshot is not None:
            existing_customer = self._storage.get_customer_by_phone(
                customer_phone_snapshot,
                tenant_id=request.tenant_id,
            )

            if existing_customer is None:
                existing_customer = self._storage.create_customer(
                    Customer(
                        tenant_id=request.tenant_id,
                        customer_id=new_id("cus"),
                        customer_name=request.customer_name,
                        customer_phone=customer_phone_snapshot,
                    )
                )

            customer_id = existing_customer.customer_id
            customer_name_snapshot = existing_customer.customer_name

        order = Order(
            tenant_id=request.tenant_id,
            order_id=order_id,
            customer_id=customer_id,
            customer_name_snapshot=customer_name_snapshot,
            customer_phone_snapshot=customer_phone_snapshot,
            raw_message=request.raw_message,
            status="draft",
            items=order_items,
            subtotal=subtotal,
            delivery_fee=delivery_fee,
            packaging_fee=packaging_fee,
            total=total,
            fulfillment_type=request.fulfillment_type,
            delivery_zone=request.delivery_zone,
            customer_notes=request.customer_notes,
            payment_method=request.payment_method,
        )
        if self._lifecycle_store is not None:
            return self._lifecycle_store.create_order_with_transition(
                order=order,
                transition=OrderStatusTransition(
                    transition_id=new_id("ost"),
                    tenant_id=order.tenant_id,
                    order_id=order.order_id,
                    from_status=None,
                    to_status="draft",
                    occurred_at=order.created_at,
                    source="system",
                ),
            )

        return self._storage.create_order(order)

    def confirm_order(
        self,
        order_id: str,
        confirmed_at: datetime | None = None,
    ) -> Order:
        order = self._storage.get_order(order_id)

        if order is None:
            raise OrderNotFoundError(order_id)

        if order.status != "draft":
            raise InvalidOrderStateError(order_id, order.status)

        confirmed_at = confirmed_at or utc_now()

        products_by_product_id = {}
        quantities_by_product_id = {}

        for item in order.items:
            if item.product_id is None:
                raise ProductNotFoundError(item.product_id)

            product = products_by_product_id.get(item.product_id)
            if product is None:
                product = self._storage.get_product(item.product_id)

                if product is None:
                    raise ProductNotFoundError(item.product_id)

                products_by_product_id[item.product_id] = product

            quantities_by_product_id[item.product_id] = (
                quantities_by_product_id.get(item.product_id, Decimal("0"))
                + item.quantity
            )

        already_applied_product_ids: set[str] = set()

        for product_id, quantity in quantities_by_product_id.items():
            movement_id = f"mov_sale_{order_id}_{product_id}"
            existing_movements = self._storage.list_stock_movements(
                product_id=product_id,
            )

            exact_match = any(
                movement.tenant_id == order.tenant_id
                and movement.stock_movement_id == movement_id
                and movement.product_id == product_id
                and movement.quantity_delta == -quantity
                and movement.reason == "sale"
                and movement.reference_id == order_id
                for movement in existing_movements
            )

            if exact_match:
                logger.warning(
                    "Repairing partially confirmed order %s: "
                    "sale stock movement already exists for product %s",
                    order_id,
                    product_id,
                )
                already_applied_product_ids.add(product_id)

        for product_id, requested_quantity in quantities_by_product_id.items():
            if product_id in already_applied_product_ids:
                continue

            product = products_by_product_id[product_id]

            if product.current_stock < requested_quantity:
                raise InsufficientStockError(
                    product.product_id,
                    requested=requested_quantity,
                    available=product.current_stock,
                )

        for product_id, quantity in quantities_by_product_id.items():
            if product_id in already_applied_product_ids:
                continue

            product = products_by_product_id[product_id]
            movement_id = f"mov_sale_{order_id}_{product_id}"

            movement = StockMovement(
                tenant_id=order.tenant_id,
                stock_movement_id=movement_id,
                created_at=confirmed_at,
                product_id=product.product_id,
                quantity_delta=-quantity,
                reason="sale",
                reference_id=order_id,
            )

            self._storage.append_stock_movement(movement)
            updated_product = product.model_copy(
                update={
                    "current_stock": product.current_stock - quantity,
                    "updated_at": utc_now(),
                },
                deep=True,
            )
            self._storage.upsert_product(updated_product)

        if self._lifecycle_store is not None:
            return self._lifecycle_store.update_order_status_with_transition(
                order_id=order_id,
                status="confirmed",
                confirmed_at=confirmed_at,
                transition=OrderStatusTransition(
                    transition_id=new_id("ost"),
                    tenant_id=order.tenant_id,
                    order_id=order.order_id,
                    from_status=order.status,
                    to_status="confirmed",
                    occurred_at=confirmed_at,
                    source="operator",
                ),
            )

        return self._storage.update_order_status(
            order_id,
            "confirmed",
            confirmed_at=confirmed_at,
        )

    def review_inbound_draft(
        self,
        *,
        order_id: str,
        tenant_id: str,
        decision: Literal["approve", "reject"],
        reviewed_at: datetime | None = None,
    ) -> Order:
        status_by_decision = {
            "approve": "approved",
            "reject": "cancelled",
        }

        if decision not in status_by_decision:
            raise ValueError(f"Invalid inbound draft review decision: {decision}")

        order = self._storage.get_order(order_id)

        if order is None or order.tenant_id != tenant_id:
            raise OrderNotFoundError(order_id)

        new_status = status_by_decision[decision]
        allowed_statuses = get_allowed_next_statuses(order)

        if new_status not in allowed_statuses:
            raise InvalidOrderTransitionError(
                order_id=order_id,
                current_status=order.status,
                new_status=new_status,
            )

        occurred_at = reviewed_at or utc_now()
        if self._lifecycle_store is not None:
            return self._lifecycle_store.update_order_status_with_transition(
                order_id=order_id,
                status=new_status,
                status_updated_at=occurred_at,
                transition=OrderStatusTransition(
                    transition_id=new_id("ost"),
                    tenant_id=order.tenant_id,
                    order_id=order.order_id,
                    from_status=order.status,
                    to_status=new_status,
                    occurred_at=occurred_at,
                    source="operator",
                ),
            )

        return self._storage.update_order_status(
            order_id,
            new_status,
            status_updated_at=occurred_at,
        )

    def confirm_approved_order(
        self,
        *,
        order_id: str,
        tenant_id: str,
        confirmed_at: datetime | None = None,
    ) -> Order:
        if self._atomic_confirmation_store is None:
            raise UnsupportedOrderConfirmationError()

        order = self._storage.get_order(order_id)

        if order is None or order.tenant_id != tenant_id:
            raise OrderNotFoundError(order_id)

        allowed_statuses = get_allowed_confirmation_statuses(order)

        if "confirmed" not in allowed_statuses:
            raise InvalidOrderTransitionError(
                order_id=order_id,
                current_status=order.status,
                new_status="confirmed",
            )

        occurred_at = confirmed_at or utc_now()
        try:
            return self._atomic_confirmation_store.confirm_order_atomically(
                order_id=order_id,
                tenant_id=tenant_id,
                expected_from_status="approved",
                transition_source="operator",
                transition_id=new_id("ost"),
                confirmed_at=occurred_at,
            )
        except StorageOrderStatusMismatchError as exc:
            raise InvalidOrderTransitionError(
                order_id=exc.order_id,
                current_status=exc.current_status,
                new_status=exc.new_status,
            ) from exc
        except StorageProductNotFoundError as exc:
            raise ProductNotFoundError(exc.product_id) from exc
        except StorageInsufficientStockError as exc:
            raise InsufficientStockError(
                exc.product_id,
                requested=exc.requested,
                available=exc.available,
            ) from exc
        except KeyError as exc:
            raise OrderNotFoundError(order_id) from exc

    def transition_order_status(
        self,
        order_id: str,
        tenant_id: str,
        new_status: str,
        reason: str | None = None,
        status_updated_at: datetime | None = None,
    ) -> Order:
        order = self._storage.get_order(order_id)

        if order is None or order.tenant_id != tenant_id:
            raise OrderNotFoundError(order_id)

        allowed_statuses = get_allowed_next_statuses(order)

        if new_status not in allowed_statuses:
            raise InvalidOrderTransitionError(
                order_id=order_id,
                current_status=order.status,
                new_status=new_status,
            )
        occurred_at = status_updated_at or utc_now()
        if self._lifecycle_store is not None:
            return self._lifecycle_store.update_order_status_with_transition(
                order_id=order_id,
                status=new_status,
                status_updated_at=occurred_at,
                transition=OrderStatusTransition(
                    transition_id=new_id("ost"),
                    tenant_id=order.tenant_id,
                    order_id=order.order_id,
                    from_status=order.status,
                    to_status=new_status,
                    occurred_at=occurred_at,
                    source="operator",
                ),
            )

        return self._storage.update_order_status(
            order_id,
            new_status,
            status_updated_at=occurred_at,
        )
