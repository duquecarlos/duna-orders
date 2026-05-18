from datetime import datetime

from duna_orders.domain.models import Order, StockMovement, utc_now
from duna_orders.services.exceptions import (
    InsufficientStockError,
    InvalidOrderStateError,
    OrderNotFoundError,
    ProductNotFoundError,
)
from duna_orders.storage.base import StorageInterface


class OrderService:
    def __init__(self, storage: StorageInterface) -> None:
        self._storage = storage

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

        products_by_item_id = {}

        for item in order.items:
            if item.product_id is None:
                raise ProductNotFoundError(item.product_id)

            product = self._storage.get_product(item.product_id)

            if product is None:
                raise ProductNotFoundError(item.product_id)

            if product.current_stock < item.quantity:
                raise InsufficientStockError(
                    product.product_id,
                    requested=item.quantity,
                    available=product.current_stock,
                )

            products_by_item_id[item.order_item_id] = product

        for item in order.items:
            product = products_by_item_id[item.order_item_id]
            movement_id = f"mov_sale_{order_id}_{item.product_id}"

            movement = StockMovement(
                stock_movement_id=movement_id,
                created_at=confirmed_at,
                product_id=product.product_id,
                quantity_delta=-item.quantity,
                reason="sale",
                related_order_id=order_id,
            )

            try:
                self._storage.append_stock_movement(movement)
            except ValueError:
                continue

            updated_product = product.model_copy(
                update={
                    "current_stock": product.current_stock - item.quantity,
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