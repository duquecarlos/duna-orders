from decimal import Decimal


class StorageError(Exception):
    """Base class for storage-layer errors."""


class StorageConfigError(StorageError):
    """Raised when storage configuration is missing or invalid."""


class StorageAuthError(StorageError):
    """Raised when storage authentication fails."""


class StorageBackendError(StorageError):
    """Raised when the storage backend fails unexpectedly."""


class DuplicateStockMovementError(StorageError):
    def __init__(self, stock_movement_id: str) -> None:
        super().__init__(f"Stock movement already exists: {stock_movement_id}")
        self.stock_movement_id = stock_movement_id


class StorageOrderStatusMismatchError(StorageError):
    def __init__(self, order_id: str, current_status: str, new_status: str) -> None:
        super().__init__(
            f"Order {order_id} cannot transition from "
            f"'{current_status}' to '{new_status}'"
        )
        self.order_id = order_id
        self.current_status = current_status
        self.new_status = new_status


class StorageProductNotFoundError(StorageError):
    def __init__(self, product_id: str | None) -> None:
        super().__init__(f"Product {product_id} not found")
        self.product_id = product_id


class StorageInsufficientStockError(StorageError):
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
