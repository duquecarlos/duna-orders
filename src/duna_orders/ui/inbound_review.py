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
LINKED_MESSAGE_DIAGNOSTIC_MESSAGE = (
    "Some linked inbound messages could not be shown because their orders are "
    "missing, belong to another tenant, or are no longer reviewable. Refresh "
    "the page; if this continues, escalate for manual review."
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


def linked_message_diagnostic_message(
    *,
    missing_order_count: int,
    tenant_mismatch_count: int,
    confirmed_count: int,
    cancelled_count: int,
    other_status_count: int,
) -> str | None:
    parts: list[str] = []

    if missing_order_count:
        parts.append(
            _count_label(
                missing_order_count,
                singular="missing order",
                plural="missing orders",
            )
        )

    if tenant_mismatch_count:
        parts.append(
            _count_label(
                tenant_mismatch_count,
                singular="tenant mismatch",
                plural="tenant mismatches",
            )
        )

    already_processed_count = confirmed_count + cancelled_count
    if already_processed_count:
        parts.append(
            _count_label(
                already_processed_count,
                singular="already processed",
                plural="already processed",
            )
        )

    if other_status_count:
        parts.append(
            _count_label(
                other_status_count,
                singular="no longer reviewable",
                plural="no longer reviewable",
            )
        )

    if not parts:
        return None

    return f"Skipped linked messages: {', '.join(parts)}. {LINKED_MESSAGE_DIAGNOSTIC_MESSAGE}"


def _count_label(count: int, *, singular: str, plural: str) -> str:
    label = singular if count == 1 else plural
    return f"{count} {label}"
