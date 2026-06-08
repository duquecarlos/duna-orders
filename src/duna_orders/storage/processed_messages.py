from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from sqlalchemy.orm import Session

from duna_orders.domain.models import utc_now
from duna_orders.storage.postgres_models import ProcessedMessageRow
from duna_orders.storage.postgres_session import session_scope


@dataclass(frozen=True)
class ProcessedMessage:
    message_sid: str
    tenant_id: str
    received_at: datetime
    from_number: str | None = None
    raw_body: str | None = None
    resulting_order_id: str | None = None


class PostgresProcessedMessageStore:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def try_record_message(
        self,
        *,
        message_sid: str,
        tenant_id: str,
        from_number: str | None = None,
        raw_body: str | None = None,
    ) -> bool:
        if not message_sid or not message_sid.strip():
            raise ValueError("message_sid is required")

        session = self._session_factory()

        try:
            session.add(
                ProcessedMessageRow(
                    message_sid=message_sid,
                    tenant_id=tenant_id,
                    received_at=utc_now(),
                    from_number=from_number,
                    raw_body=raw_body,
                    resulting_order_id=None,
                )
            )
            session.commit()
            return True
        except IntegrityError:
            session.rollback()
            return False
        finally:
            session.close()

    def mark_order_created(self, *, message_sid: str, order_id: str) -> None:
        with session_scope(self._session_factory) as session:
            row = session.get(ProcessedMessageRow, message_sid)

            if row is None:
                raise ValueError(f"Processed message not found: {message_sid}")

            row.resulting_order_id = order_id

    def get_message(self, message_sid: str) -> ProcessedMessage | None:
        with session_scope(self._session_factory) as session:
            row = session.get(ProcessedMessageRow, message_sid)

            if row is None:
                return None

            return _message_from_row(row)

    def get_message_for_order(
        self,
        *,
        order_id: str,
        tenant_id: str,
    ) -> ProcessedMessage | None:
        with session_scope(self._session_factory) as session:
            row = session.scalar(
                select(ProcessedMessageRow)
                .where(ProcessedMessageRow.tenant_id == tenant_id)
                .where(ProcessedMessageRow.resulting_order_id == order_id)
                .order_by(
                    ProcessedMessageRow.received_at.desc(),
                    ProcessedMessageRow.message_sid,
                )
            )

            if row is None:
                return None

            return _message_from_row(row)

    def list_messages_with_resulting_order(
        self,
        *,
        tenant_id: str,
    ) -> list[ProcessedMessage]:
        with session_scope(self._session_factory) as session:
            rows = session.scalars(
                select(ProcessedMessageRow)
                .where(ProcessedMessageRow.tenant_id == tenant_id)
                .where(ProcessedMessageRow.resulting_order_id.is_not(None))
                .order_by(
                    ProcessedMessageRow.received_at.desc(),
                    ProcessedMessageRow.message_sid,
                )
            ).all()

            return [_message_from_row(row) for row in rows]


def _message_from_row(row: ProcessedMessageRow) -> ProcessedMessage:
    return ProcessedMessage(
        message_sid=row.message_sid,
        tenant_id=row.tenant_id,
        received_at=row.received_at,
        from_number=row.from_number,
        raw_body=row.raw_body,
        resulting_order_id=row.resulting_order_id,
    )
