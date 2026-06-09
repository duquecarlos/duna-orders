from __future__ import annotations

from duna_orders.services.exceptions import (
    InsufficientStockError,
    InvalidOrderTransitionError,
    OrderNotFoundError,
    ProductNotFoundError,
    UnsupportedOrderConfirmationError,
)
from duna_orders.storage.exceptions import DuplicateStockMovementError


STALE_ORDER_MESSAGE = (
    "The order status changed before this action was completed. "
    "Refresh the page and review the latest status."
)
LINKED_ORDER_NOT_FOUND_MESSAGE = (
    "The linked order could not be found for this tenant. Refresh the page; "
    "if it still appears missing, escalate for manual review."
)
INSUFFICIENT_STOCK_MESSAGE = (
    "This order cannot be confirmed because one or more products do not have "
    "enough stock. Review inventory before trying again."
)
MISSING_PRODUCT_MESSAGE = (
    "This order cannot be confirmed because one or more products are missing "
    "from the catalog. Review the catalog before trying again."
)
DUPLICATE_STOCK_MOVEMENT_MESSAGE = (
    "This order appears already confirmed or has existing stock movements. "
    "Do not retry; escalate for manual review."
)
POSTGRES_ONLY_MESSAGE = (
    "Inbound review and approved-order confirmation are available only with "
    "the Postgres backend."
)
LIST_LOAD_FAILURE_MESSAGE = (
    "Inbound review could not be loaded. Refresh the page; if the problem "
    "continues, escalate for manual review."
)
ACTION_FAILURE_MESSAGE = (
    "This action could not be completed. Refresh the page and try again; "
    "if the problem continues, escalate for manual review."
)


def operator_action_error_message(error: Exception) -> str:
    if isinstance(error, InvalidOrderTransitionError):
        return STALE_ORDER_MESSAGE

    if isinstance(error, OrderNotFoundError):
        return LINKED_ORDER_NOT_FOUND_MESSAGE

    if isinstance(error, InsufficientStockError):
        return INSUFFICIENT_STOCK_MESSAGE

    if isinstance(error, ProductNotFoundError):
        return MISSING_PRODUCT_MESSAGE

    if isinstance(error, DuplicateStockMovementError):
        return DUPLICATE_STOCK_MOVEMENT_MESSAGE

    if isinstance(error, UnsupportedOrderConfirmationError):
        return POSTGRES_ONLY_MESSAGE

    return ACTION_FAILURE_MESSAGE


def operator_list_load_error_message(error: Exception) -> str:
    return LIST_LOAD_FAILURE_MESSAGE
