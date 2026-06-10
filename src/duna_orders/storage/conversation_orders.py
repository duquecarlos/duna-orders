from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from duna_orders.domain.models import Order
from duna_orders.storage.postgres import _order_from_row
from duna_orders.storage.postgres_models import OrderRow
from duna_orders.storage.postgres_session import session_scope


class ConversationOrderLookup(Protocol):
    def get_order_by_conversation_id(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
    ) -> Order | None:
        ...


class PostgresConversationOrderLookup:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def get_order_by_conversation_id(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
    ) -> Order | None:
        _require_text(tenant_id, "tenant_id")
        _require_text(conversation_id, "conversation_id")

        with session_scope(self._session_factory) as session:
            row = session.scalar(
                select(OrderRow)
                .options(selectinload(OrderRow.items))
                .where(OrderRow.tenant_id == tenant_id)
                .where(OrderRow.conversation_id == conversation_id)
            )

            return _order_from_row(row) if row is not None else None


def _require_text(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name} is required")
