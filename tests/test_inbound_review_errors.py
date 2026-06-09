from __future__ import annotations

from decimal import Decimal

from duna_orders.services.exceptions import (
    InsufficientStockError,
    InvalidOrderTransitionError,
    OrderNotFoundError,
    ProductNotFoundError,
    UnsupportedOrderConfirmationError,
)
from duna_orders.storage.exceptions import DuplicateStockMovementError
from duna_orders.ui.inbound_review import (
    ACTION_FAILURE_MESSAGE,
    DUPLICATE_STOCK_MOVEMENT_MESSAGE,
    INSUFFICIENT_STOCK_MESSAGE,
    LINKED_ORDER_NOT_FOUND_MESSAGE,
    LINKED_MESSAGE_DIAGNOSTIC_MESSAGE,
    LIST_LOAD_FAILURE_MESSAGE,
    MISSING_PRODUCT_MESSAGE,
    linked_message_diagnostic_message,
    operator_action_error_message,
    operator_list_load_error_message,
    POSTGRES_ONLY_MESSAGE,
    STALE_ORDER_MESSAGE,
)


def test_operator_error_message_maps_stale_transition() -> None:
    error = InvalidOrderTransitionError(
        order_id="ord_test",
        current_status="confirmed",
        new_status="approved",
    )

    assert operator_action_error_message(error) == STALE_ORDER_MESSAGE


def test_operator_error_message_maps_missing_order() -> None:
    assert (
        operator_action_error_message(OrderNotFoundError("ord_test"))
        == LINKED_ORDER_NOT_FOUND_MESSAGE
    )


def test_operator_error_message_maps_insufficient_stock() -> None:
    error = InsufficientStockError(
        "prd_test",
        requested=Decimal("2"),
        available=Decimal("1"),
    )

    assert operator_action_error_message(error) == INSUFFICIENT_STOCK_MESSAGE


def test_operator_error_message_maps_missing_product() -> None:
    assert (
        operator_action_error_message(ProductNotFoundError("prd_test"))
        == MISSING_PRODUCT_MESSAGE
    )


def test_operator_error_message_maps_duplicate_sale_movement() -> None:
    error = DuplicateStockMovementError("mov_sale_ord_test_prd_test")

    assert operator_action_error_message(error) == DUPLICATE_STOCK_MOVEMENT_MESSAGE
    assert "Do not retry; escalate" in operator_action_error_message(error)
    assert "mov_sale_ord_test_prd_test" not in operator_action_error_message(error)


def test_operator_error_message_maps_unsupported_confirmation_backend() -> None:
    assert (
        operator_action_error_message(UnsupportedOrderConfirmationError())
        == POSTGRES_ONLY_MESSAGE
    )


def test_operator_error_message_maps_unknown_action_error_to_generic_message() -> None:
    assert operator_action_error_message(RuntimeError("sql exploded")) == (
        ACTION_FAILURE_MESSAGE
    )


def test_operator_list_load_error_message_is_generic() -> None:
    assert operator_list_load_error_message(RuntimeError("database detail")) == (
        LIST_LOAD_FAILURE_MESSAGE
    )


def test_linked_message_diagnostic_message_reports_safe_counts() -> None:
    message = linked_message_diagnostic_message(
        missing_order_count=1,
        tenant_mismatch_count=2,
        confirmed_count=1,
        cancelled_count=1,
        other_status_count=1,
    )

    assert message is not None
    assert "Skipped linked messages: 1 missing order" in message
    assert "2 tenant mismatches" in message
    assert "2 already processed" in message
    assert "1 no longer reviewable" in message
    assert LINKED_MESSAGE_DIAGNOSTIC_MESSAGE in message
    assert "ord_" not in message
    assert "SM_" not in message


def test_linked_message_diagnostic_message_is_empty_without_skipped_counts() -> None:
    assert (
        linked_message_diagnostic_message(
            missing_order_count=0,
            tenant_mismatch_count=0,
            confirmed_count=0,
            cancelled_count=0,
            other_status_count=0,
        )
        is None
    )
