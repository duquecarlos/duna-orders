from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from duna_orders.domain.models import Order, OrderStatusTransition, utc_now
from duna_orders.storage.postgres import _order_from_row, _order_to_row
from duna_orders.storage.postgres_models import OrderRow, OrderStatusTransitionRow
from duna_orders.storage.postgres_session import session_scope

class OrderLifecycleStore(Protocol):
    def create_order_with_transition(
        self,
        *,
        order: Order,
        transition: OrderStatusTransition,
    ) -> Order:
        pass

    def update_order_status_with_transition(
        self,
        *,
        order_id: str,
        status: str,
        transition: OrderStatusTransition,
        confirmed_at: datetime | None = None,
        status_updated_at: datetime | None = None,
    ) -> Order:
        pass

    def list_order_status_transitions(
        self,
        *,
        order_id: str,
        tenant_id: str,
    ) -> list[OrderStatusTransition]:
        pass
class PostgresOrderLifecycleStore:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def create_order_with_transition(
        self,
        *,
        order: Order,
        transition: OrderStatusTransition,
    ) -> Order:
        with session_scope(self._session_factory) as session:
            existing = session.get(OrderRow, order.order_id)

            if existing is not None:
                raise ValueError(f"Order already exists: {order.order_id}")

            order_row = _order_to_row(order)
            transition_row = _transition_to_row(transition)

            session.add(order_row)
            session.add(transition_row)
            session.flush()

            return _order_from_row(order_row)

    def update_order_status_with_transition(
        self,
        *,
        order_id: str,
        status: str,
        transition: OrderStatusTransition,
        confirmed_at: datetime | None = None,
        status_updated_at: datetime | None = None,
    ) -> Order:
        with session_scope(self._session_factory) as session:
            row = session.scalar(
                select(OrderRow)
                .options(selectinload(OrderRow.items))
                .where(OrderRow.order_id == order_id)
            )

            if row is None:
                raise KeyError(f"Order not found: {order_id}")

            now = utc_now()
            row.status = status
            row.updated_at = now
            row.status_updated_at = status_updated_at or confirmed_at or now

            if confirmed_at is not None:
                row.confirmed_at = confirmed_at

            session.add(_transition_to_row(transition))
            session.flush()

            return _order_from_row(row)

    def append_transition(
        self,
        transition: OrderStatusTransition,
    ) -> OrderStatusTransition:
        with session_scope(self._session_factory) as session:
            row = _transition_to_row(transition)
            session.add(row)

            try:
                session.flush()
            except IntegrityError as error:
                raise ValueError(
                    f"Order status transition already exists: {transition.transition_id}"
                ) from error

            return _transition_from_row(row)

    def list_order_status_transitions(
        self,
        *,
        order_id: str,
        tenant_id: str,
    ) -> list[OrderStatusTransition]:
        with session_scope(self._session_factory) as session:
            rows = session.scalars(
                select(OrderStatusTransitionRow)
                .where(OrderStatusTransitionRow.tenant_id == tenant_id)
                .where(OrderStatusTransitionRow.order_id == order_id)
                .order_by(OrderStatusTransitionRow.occurred_at)
            ).all()

            return [_transition_from_row(row) for row in rows]


def _transition_to_row(
    transition: OrderStatusTransition,
) -> OrderStatusTransitionRow:
    return OrderStatusTransitionRow(
        transition_id=transition.transition_id,
        tenant_id=transition.tenant_id,
        order_id=transition.order_id,
        from_status=transition.from_status,
        to_status=transition.to_status,
        occurred_at=transition.occurred_at,
        source=transition.source,
    )


def _transition_from_row(
    row: OrderStatusTransitionRow,
) -> OrderStatusTransition:
    return OrderStatusTransition(
        transition_id=row.transition_id,
        tenant_id=row.tenant_id,
        order_id=row.order_id,
        from_status=row.from_status,
        to_status=row.to_status,
        occurred_at=row.occurred_at,
        source=row.source,
    )