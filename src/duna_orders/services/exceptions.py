from decimal import Decimal


class ServiceError(Exception):
    pass


class OrderNotFoundError(ServiceError):
    def __init__(self, order_id: str) -> None:
        super().__init__(f"Order {order_id} not found")
        self.order_id = order_id


class InvalidOrderStateError(ServiceError):
    def __init__(self, order_id: str, status: str) -> None:
        super().__init__(
            f"Order {order_id} cannot be confirmed from status '{status}' "
            f"(must be 'draft')"
        )
        self.order_id = order_id
        self.status = status


class ProductNotFoundError(ServiceError):
    def __init__(self, product_id: str | None) -> None:
        super().__init__(f"Product {product_id} not found")
        self.product_id = product_id


class InsufficientStockError(ServiceError):
    def __init__(
        self,
        product_id: str,
        requested: Decimal,
        available: Decimal,
    ) -> None:
        super().__init__(
            f"Product {product_id}: requested {requested}, available {available}"
        )
        self.product_id = product_id
        self.requested = requested
        self.available = available