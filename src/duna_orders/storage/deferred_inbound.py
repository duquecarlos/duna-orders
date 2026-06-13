from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from duna_orders.domain.models import utc_now
from duna_orders.storage.postgres_models import DeferredInboundRow
from duna_orders.storage.postgres_session import session_scope


@dataclass(frozen=True)
class DeferredInboundRecord:
    message_sid: str
    tenant_id: str
    customer_key: str
    from_number: str
    raw_body: str
    received_at: datetime
    deferred_at: datetime
    processed_at: datetime | None
    processing_started_at: datetime | None
    attempt_count: int


class DeferredInboundStore(Protocol):
    def defer_message(
        self,
        *,
        message_sid: str,
        tenant_id: str,
        customer_key: str,
        from_number: str,
        raw_body: str,
        received_at: datetime,
    ) -> bool: ...

    def has_pending(self, *, tenant_id: str, customer_key: str) -> bool: ...

    def list_pending_for_customer(
        self,
        *,
        tenant_id: str,
        customer_key: str,
        limit: int | None = None,
    ) -> list[DeferredInboundRecord]: ...

    def list_pending_for_tenant(
        self,
        *,
        tenant_id: str,
        limit: int | None = None,
    ) -> list[DeferredInboundRecord]: ...

    def mark_processing_started(self, *, message_sid: str) -> bool: ...

    def mark_processed(
        self,
        *,
        message_sid: str,
        processed_at: datetime | None = None,
    ) -> bool: ...


class PostgresDeferredInboundStore:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def defer_message(
        self,
        *,
        message_sid: str,
        tenant_id: str,
        customer_key: str,
        from_number: str,
        raw_body: str,
        received_at: datetime,
    ) -> bool:
        _require_text(message_sid, "message_sid")
        _require_text(tenant_id, "tenant_id")
        _require_text(customer_key, "customer_key")

        with session_scope(self._session_factory) as session:
            row = session.execute(
                text(
                    """
                    INSERT INTO deferred_inbound (
                        message_sid, tenant_id, customer_key, from_number,
                        raw_body, received_at, deferred_at, processed_at,
                        processing_started_at, attempt_count
                    )
                    VALUES (
                        :message_sid, :tenant_id, :customer_key, :from_number,
                        :raw_body, :received_at, :deferred_at, NULL,
                        NULL, 0
                    )
                    ON CONFLICT (message_sid) DO NOTHING
                    RETURNING message_sid
                    """
                ),
                {
                    "message_sid": message_sid,
                    "tenant_id": tenant_id,
                    "customer_key": customer_key,
                    "from_number": from_number,
                    "raw_body": raw_body,
                    "received_at": received_at,
                    "deferred_at": utc_now(),
                },
            ).first()

        return row is not None

    def has_pending(self, *, tenant_id: str, customer_key: str) -> bool:
        _require_text(tenant_id, "tenant_id")
        _require_text(customer_key, "customer_key")

        stmt = (
            select(DeferredInboundRow.message_sid)
            .where(
                DeferredInboundRow.tenant_id == tenant_id,
                DeferredInboundRow.customer_key == customer_key,
                DeferredInboundRow.processed_at.is_(None),
            )
            .limit(1)
        )

        with session_scope(self._session_factory) as session:
            return session.scalar(stmt) is not None

    def list_pending_for_customer(
        self,
        *,
        tenant_id: str,
        customer_key: str,
        limit: int | None = None,
    ) -> list[DeferredInboundRecord]:
        _require_text(tenant_id, "tenant_id")
        _require_text(customer_key, "customer_key")

        stmt = (
            select(DeferredInboundRow)
            .where(
                DeferredInboundRow.tenant_id == tenant_id,
                DeferredInboundRow.customer_key == customer_key,
                DeferredInboundRow.processed_at.is_(None),
            )
            .order_by(
                DeferredInboundRow.received_at.asc(),
                DeferredInboundRow.deferred_at.asc(),
                DeferredInboundRow.message_sid.asc(),
            )
        )

        if limit is not None:
            stmt = stmt.limit(limit)

        with session_scope(self._session_factory) as session:
            rows = session.scalars(stmt).all()
            return [_record_from_row(row) for row in rows]

    def list_pending_for_tenant(
        self,
        *,
        tenant_id: str,
        limit: int | None = None,
    ) -> list[DeferredInboundRecord]:
        _require_text(tenant_id, "tenant_id")

        stmt = (
            select(DeferredInboundRow)
            .where(
                DeferredInboundRow.tenant_id == tenant_id,
                DeferredInboundRow.processed_at.is_(None),
            )
            .order_by(
                DeferredInboundRow.received_at.asc(),
                DeferredInboundRow.deferred_at.asc(),
                DeferredInboundRow.message_sid.asc(),
            )
        )

        if limit is not None:
            stmt = stmt.limit(limit)

        with session_scope(self._session_factory) as session:
            rows = session.scalars(stmt).all()
            return [_record_from_row(row) for row in rows]

    def mark_processing_started(self, *, message_sid: str) -> bool:
        _require_text(message_sid, "message_sid")

        with session_scope(self._session_factory) as session:
            row = session.execute(
                text(
                    """
                    UPDATE deferred_inbound
                    SET processing_started_at = :now,
                        attempt_count = attempt_count + 1
                    WHERE message_sid = :message_sid
                      AND processed_at IS NULL
                    RETURNING message_sid
                    """
                ),
                {"message_sid": message_sid, "now": utc_now()},
            ).first()

        return row is not None

    def mark_processed(
        self,
        *,
        message_sid: str,
        processed_at: datetime | None = None,
    ) -> bool:
        _require_text(message_sid, "message_sid")

        with session_scope(self._session_factory) as session:
            row = session.execute(
                text(
                    """
                    UPDATE deferred_inbound
                    SET processed_at = :processed_at
                    WHERE message_sid = :message_sid
                      AND processed_at IS NULL
                    RETURNING message_sid
                    """
                ),
                {
                    "message_sid": message_sid,
                    "processed_at": processed_at or utc_now(),
                },
            ).first()

        return row is not None


def _record_from_row(row: DeferredInboundRow) -> DeferredInboundRecord:
    return DeferredInboundRecord(
        message_sid=row.message_sid,
        tenant_id=row.tenant_id,
        customer_key=row.customer_key,
        from_number=row.from_number,
        raw_body=row.raw_body,
        received_at=row.received_at,
        deferred_at=row.deferred_at,
        processed_at=row.processed_at,
        processing_started_at=row.processing_started_at,
        attempt_count=row.attempt_count,
    )


def _require_text(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name} is required")
