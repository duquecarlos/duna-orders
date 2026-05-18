class DunaOrdersError(Exception):
    """Base exception for duna-orders domain errors."""


class ProductNotFoundError(DunaOrdersError):
    pass


class CustomerNotFoundError(DunaOrdersError):
    pass


class OrderNotFoundError(DunaOrdersError):
    pass


class InsufficientStockError(DunaOrdersError):
    pass


class InvalidOrderStateError(DunaOrdersError):
    pass