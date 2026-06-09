from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from duna_orders.domain.models import Order, utc_now
from duna_orders.storage.exceptions import (
    DuplicateStockMovementError,
    StorageInsufficientStockError,
    StorageOrderStatusMismatchError,
    StorageProductNotFoundError,
)
from duna_orders.storage.postgres import _order_from_row
from duna_orders.storage.postgres_models import (
    OrderRow,
    OrderStatusTransitionRow,
    ProductRow,
    StockMovementRow,
)
from duna_orders.storage.postgres_session import session_scope


class PostgresAtomicOrderConfirmationStore:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        fail_after_stock_movements_for_test: bool = False,
    ) -> None:
        self._session_factory = session_factory
        self._fail_after_stock_movements_for_test = fail_after_stock_movements_for_test

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
        with session_scope(self._session_factory) as session:
            order_row = session.scalar(
                select(OrderRow)
                .options(selectinload(OrderRow.items))
                .where(OrderRow.order_id == order_id)
                .where(OrderRow.tenant_id == tenant_id)
            )

            if order_row is None:
                raise KeyError(f"Order not found: {order_id}")

            if order_row.status != expected_from_status:
                raise StorageOrderStatusMismatchError(
                    order_id=order_id,
                    current_status=order_row.status,
                    new_status="confirmed",
                )

            quantities_by_product_id = _quantities_by_product_id(order_row)
            product_rows_by_id = _product_rows_by_id(
                session,
                tenant_id=tenant_id,
                product_ids=tuple(quantities_by_product_id),
            )

            _raise_for_existing_sale_movements(
                session,
                tenant_id=tenant_id,
                order_id=order_id,
                quantities_by_product_id=quantities_by_product_id,
            )
            _validate_stock(
                product_rows_by_id=product_rows_by_id,
                quantities_by_product_id=quantities_by_product_id,
            )

            for product_id, quantity in quantities_by_product_id.items():
                session.add(
                    StockMovementRow(
                        tenant_id=tenant_id,
                        stock_movement_id=_sale_movement_id(order_id, product_id),
                        created_at=confirmed_at,
                        product_id=product_id,
                        quantity_delta=-quantity,
                        reason="sale",
                        reference_id=order_id,
                    )
                )

            session.flush()

            if self._fail_after_stock_movements_for_test:
                raise RuntimeError("simulated atomic confirmation failure")

            now = utc_now()

            for product_id, quantity in quantities_by_product_id.items():
                product_row = product_rows_by_id[product_id]
                product_row.current_stock -= quantity
                product_row.updated_at = now

            order_row.status = "confirmed"
            order_row.confirmed_at = confirmed_at
            order_row.status_updated_at = confirmed_at
            order_row.updated_at = now

            session.add(
                OrderStatusTransitionRow(
                    transition_id=transition_id,
                    tenant_id=tenant_id,
                    order_id=order_id,
                    from_status=expected_from_status,
                    to_status="confirmed",
                    occurred_at=confirmed_at,
                    source=transition_source,
                )
            )
            session.flush()

            return _order_from_row(order_row)


def _quantities_by_product_id(order_row: OrderRow) -> dict[str, Decimal]:
    quantities_by_product_id: dict[str, Decimal] = {}

    for item in order_row.items:
        if item.product_id is None:
            raise StorageProductNotFoundError(item.product_id)

        quantities_by_product_id[item.product_id] = (
            quantities_by_product_id.get(item.product_id, Decimal("0"))
            + item.quantity
        )

    return quantities_by_product_id


def _product_rows_by_id(
    session: Session,
    *,
    tenant_id: str,
    product_ids: tuple[str, ...],
) -> dict[str, ProductRow]:
    if not product_ids:
        return {}

    rows = session.scalars(
        select(ProductRow)
        .where(ProductRow.tenant_id == tenant_id)
        .where(ProductRow.product_id.in_(product_ids))
    ).all()
    product_rows_by_id = {row.product_id: row for row in rows}

    for product_id in product_ids:
        if product_id not in product_rows_by_id:
            raise StorageProductNotFoundError(product_id)

    return product_rows_by_id


def _raise_for_existing_sale_movements(
    session: Session,
    *,
    tenant_id: str,
    order_id: str,
    quantities_by_product_id: dict[str, Decimal],
) -> None:
    for product_id in quantities_by_product_id:
        stock_movement_id = _sale_movement_id(order_id, product_id)
        existing = session.get(StockMovementRow, stock_movement_id)

        if existing is not None and existing.tenant_id == tenant_id:
            raise DuplicateStockMovementError(stock_movement_id)


def _validate_stock(
    *,
    product_rows_by_id: dict[str, ProductRow],
    quantities_by_product_id: dict[str, Decimal],
) -> None:
    for product_id, requested_quantity in quantities_by_product_id.items():
        product_row = product_rows_by_id[product_id]

        if product_row.current_stock < requested_quantity:
            raise StorageInsufficientStockError(
                product_id,
                requested=requested_quantity,
                available=product_row.current_stock,
            )


def _sale_movement_id(order_id: str, product_id: str) -> str:
    return f"mov_sale_{order_id}_{product_id}"
