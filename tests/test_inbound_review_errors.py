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
    LIST_LOAD_FAILURE_MESSAGE,
    MISSING_PRODUCT_MESSAGE,
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
