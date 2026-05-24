from datetime import datetime
from decimal import Decimal

from duna_orders.domain.models import (
    DraftOrderRequest,
    Order,
    OrderItem,
    StockMovement,
    utc_now,
)
from duna_orders.ids import new_id
from duna_orders.services.exceptions import (
    EmptyDraftError,
    InactiveProductError,
    InsufficientStockError,
    InvalidOrderStateError,
    InvalidOrderTransitionError,
    OrderNotFoundError,
    ProductNotFoundError,
)
from duna_orders.storage.base import StorageInterface

BASE_STATUS_TRANSITIONS = {
    "confirmed": ("in_preparation", "cancelled"),
    "in_preparation": ("ready", "cancelled"),
}


def get_allowed_next_statuses(order: Order) -> tuple[str, ...]:
    if order.status == "ready":
        if order.fulfillment_type == "delivery":
            return ("delivered", "cancelled")

        if order.fulfillment_type == "pickup":
            return ("picked_up", "cancelled")

        return ("cancelled",)

    return BASE_STATUS_TRANSITIONS.get(order.status, ())

class OrderService:
    def __init__(self, storage: StorageInterface) -> None:
        self._storage = storage

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

        order = Order(
            tenant_id=request.tenant_id,
            order_id=order_id,
            customer_id=None,
            customer_name_snapshot=request.customer_name,
            customer_phone_snapshot=request.customer_phone,
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
                quantities_by_product_id.get(item.product_id, Decimal("0")) + item.quantity
            )

        for product_id, requested_quantity in quantities_by_product_id.items():
            product = products_by_product_id[product_id]

            if product.current_stock < requested_quantity:
                raise InsufficientStockError(
                    product.product_id,
                    requested=requested_quantity,
                    available=product.current_stock,
                )

        for product_id, quantity in quantities_by_product_id.items():
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

            try:
                self._storage.append_stock_movement(movement)
            except ValueError:
                continue

            updated_product = product.model_copy(
                update={
                    "current_stock": product.current_stock - quantity,
                    "updated_at": utc_now(),
                },
                deep=True,
            )
            self._storage.upsert_product(updated_product)

        return self._storage.update_order_status(
            order_id,
            "confirmed",
            confirmed_at=confirmed_at,
        )
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

        return self._storage.update_order_status(
            order_id,
            new_status,
            status_updated_at=status_updated_at or utc_now(),
        )