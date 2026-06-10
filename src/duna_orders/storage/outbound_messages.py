from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Protocol

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from duna_orders.domain.models import utc_now
from duna_orders.ids import new_id
from duna_orders.storage.postgres_models import OutboundMessageRow
from duna_orders.storage.postgres_session import session_scope


ORDER_CONFIRMED_ACK = "order_confirmed_ack"
OUTBOUND_PROVIDER_TWILIO = "twilio"

AcknowledgementType = Literal["order_confirmed_ack"]
OutboundMessageStatus = Literal[
    "send_requested",
    "sending",
    "sent",
    "failed",
    "unknown",
]
ClaimReason = Literal[
    "created",
    "retry_claimed",
    "suppressed_existing",
    "suppressed_failed_without_retry",
    "suppressed_in_progress",
    "suppressed_sent",
    "suppressed_unknown",
]


@dataclass(frozen=True)
class OutboundAcknowledgement:
    outbound_message_id: str
    tenant_id: str
    order_id: str
    acknowledgement_type: str
    to_number: str
    from_number: str
    body: str
    status: str
    provider: str
    attempt_count: int
    requested_by: str
    created_at: datetime
    updated_at: datetime
    provider_message_sid: str | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    sent_at: datetime | None = None


@dataclass(frozen=True)
class OutboundClaimResult:
    acknowledgement: OutboundAcknowledgement
    claimed_for_send: bool
    reason: ClaimReason


class OutboundAcknowledgementStore(Protocol):
    def claim_order_acknowledgement_for_send(
        self,
        *,
        tenant_id: str,
        order_id: str,
        acknowledgement_type: str,
        to_number: str,
        from_number: str,
        body: str,
        requested_by: str,
        retry_failed: bool = False,
    ) -> OutboundClaimResult:
        ...

    def get_for_order_acknowledgement(
        self,
        *,
        tenant_id: str,
        order_id: str,
        acknowledgement_type: str,
    ) -> OutboundAcknowledgement | None:
        ...

    def mark_sent(
        self,
        *,
        outbound_message_id: str,
        provider_message_sid: str,
    ) -> OutboundAcknowledgement:
        ...

    def mark_failed(
        self,
        *,
        outbound_message_id: str,
        error_code: str | None,
        error_message: str,
    ) -> OutboundAcknowledgement:
        ...

    def mark_unknown(
        self,
        *,
        outbound_message_id: str,
        error_code: str | None,
        error_message: str,
    ) -> OutboundAcknowledgement:
        ...


class PostgresOutboundAcknowledgementStore:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def claim_order_acknowledgement_for_send(
        self,
        *,
        tenant_id: str,
        order_id: str,
        acknowledgement_type: str,
        to_number: str,
        from_number: str,
        body: str,
        requested_by: str,
        retry_failed: bool = False,
    ) -> OutboundClaimResult:
        _require_text(tenant_id, "tenant_id")
        _require_text(order_id, "order_id")
        _require_text(acknowledgement_type, "acknowledgement_type")
        _require_text(to_number, "to_number")
        _require_text(from_number, "from_number")
        _require_text(body, "body")
        _require_text(requested_by, "requested_by")

        try:
            return self._try_create_claim(
                tenant_id=tenant_id,
                order_id=order_id,
                acknowledgement_type=acknowledgement_type,
                to_number=to_number,
                from_number=from_number,
                body=body,
                requested_by=requested_by,
            )
        except IntegrityError:
            pass

        existing = self.get_for_order_acknowledgement(
            tenant_id=tenant_id,
            order_id=order_id,
            acknowledgement_type=acknowledgement_type,
        )

        if existing is None:
            raise RuntimeError("Outbound acknowledgement uniqueness conflict without row")

        if existing.status == "failed":
            if not retry_failed:
                return OutboundClaimResult(
                    acknowledgement=existing,
                    claimed_for_send=False,
                    reason="suppressed_failed_without_retry",
                )

            return self._try_claim_failed_retry(
                outbound_message_id=existing.outbound_message_id,
                to_number=to_number,
                from_number=from_number,
                body=body,
                requested_by=requested_by,
            )

        return OutboundClaimResult(
            acknowledgement=existing,
            claimed_for_send=False,
            reason=_suppression_reason(existing.status),
        )

    def get_for_order_acknowledgement(
        self,
        *,
        tenant_id: str,
        order_id: str,
        acknowledgement_type: str,
    ) -> OutboundAcknowledgement | None:
        with session_scope(self._session_factory) as session:
            row = _get_for_order_acknowledgement_row(
                session,
                tenant_id=tenant_id,
                order_id=order_id,
                acknowledgement_type=acknowledgement_type,
            )

            return _acknowledgement_from_row(row) if row is not None else None

    def mark_sent(
        self,
        *,
        outbound_message_id: str,
        provider_message_sid: str,
    ) -> OutboundAcknowledgement:
        _require_text(provider_message_sid, "provider_message_sid")
        with session_scope(self._session_factory) as session:
            row = _get_row_by_id(session, outbound_message_id)
            _require_sending(row, "sent")
            now = utc_now()
            row.status = "sent"
            row.provider_message_sid = provider_message_sid
            row.sent_at = now
            row.updated_at = now
            row.last_error_code = None
            row.last_error_message = None
            session.flush()

            return _acknowledgement_from_row(row)

    def mark_failed(
        self,
        *,
        outbound_message_id: str,
        error_code: str | None,
        error_message: str,
    ) -> OutboundAcknowledgement:
        return self._mark_terminal_error(
            outbound_message_id=outbound_message_id,
            status="failed",
            error_code=error_code,
            error_message=error_message,
        )

    def mark_unknown(
        self,
        *,
        outbound_message_id: str,
        error_code: str | None,
        error_message: str,
    ) -> OutboundAcknowledgement:
        return self._mark_terminal_error(
            outbound_message_id=outbound_message_id,
            status="unknown",
            error_code=error_code,
            error_message=error_message,
        )

    def _try_create_claim(
        self,
        *,
        tenant_id: str,
        order_id: str,
        acknowledgement_type: str,
        to_number: str,
        from_number: str,
        body: str,
        requested_by: str,
    ) -> OutboundClaimResult:
        session = self._session_factory()

        try:
            now = utc_now()
            row = OutboundMessageRow(
                outbound_message_id=new_id("out"),
                tenant_id=tenant_id,
                order_id=order_id,
                acknowledgement_type=acknowledgement_type,
                to_number=to_number,
                from_number=from_number,
                body=body,
                status="sending",
                provider=OUTBOUND_PROVIDER_TWILIO,
                attempt_count=1,
                requested_by=requested_by,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            return OutboundClaimResult(
                acknowledgement=_acknowledgement_from_row(row),
                claimed_for_send=True,
                reason="created",
            )
        except IntegrityError:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _try_claim_failed_retry(
        self,
        *,
        outbound_message_id: str,
        to_number: str,
        from_number: str,
        body: str,
        requested_by: str,
    ) -> OutboundClaimResult:
        with session_scope(self._session_factory) as session:
            now = utc_now()
            result = session.execute(
                update(OutboundMessageRow)
                .where(OutboundMessageRow.outbound_message_id == outbound_message_id)
                .where(OutboundMessageRow.status == "failed")
                .values(
                    to_number=to_number,
                    from_number=from_number,
                    body=body,
                    requested_by=requested_by,
                    status="sending",
                    attempt_count=OutboundMessageRow.attempt_count + 1,
                    last_error_code=None,
                    last_error_message=None,
                    provider_message_sid=None,
                    sent_at=None,
                    updated_at=now,
                )
            )
            row = _get_row_by_id(session, outbound_message_id)

            if result.rowcount == 1:
                return OutboundClaimResult(
                    acknowledgement=_acknowledgement_from_row(row),
                    claimed_for_send=True,
                    reason="retry_claimed",
                )

            return OutboundClaimResult(
                acknowledgement=_acknowledgement_from_row(row),
                claimed_for_send=False,
                reason=_suppression_reason(row.status),
            )

    def _mark_terminal_error(
        self,
        *,
        outbound_message_id: str,
        status: Literal["failed", "unknown"],
        error_code: str | None,
        error_message: str,
    ) -> OutboundAcknowledgement:
        _require_text(error_message, "error_message")
        with session_scope(self._session_factory) as session:
            row = _get_row_by_id(session, outbound_message_id)
            _require_sending(row, status)
            row.status = status
            row.last_error_code = error_code
            row.last_error_message = error_message
            row.updated_at = utc_now()
            session.flush()

            return _acknowledgement_from_row(row)


def _get_for_order_acknowledgement_row(
    session: Session,
    *,
    tenant_id: str,
    order_id: str,
    acknowledgement_type: str,
) -> OutboundMessageRow | None:
    return session.scalar(
        select(OutboundMessageRow)
        .where(OutboundMessageRow.tenant_id == tenant_id)
        .where(OutboundMessageRow.order_id == order_id)
        .where(OutboundMessageRow.acknowledgement_type == acknowledgement_type)
    )


def _get_row_by_id(session: Session, outbound_message_id: str) -> OutboundMessageRow:
    row = session.get(OutboundMessageRow, outbound_message_id)

    if row is None:
        raise ValueError(f"Outbound acknowledgement not found: {outbound_message_id}")

    return row


def _require_sending(row: OutboundMessageRow, target_status: str) -> None:
    if row.status != "sending":
        raise ValueError(
            "Outbound acknowledgement must be sending before marking "
            f"{target_status}; current status is {row.status}"
        )


def _acknowledgement_from_row(row: OutboundMessageRow) -> OutboundAcknowledgement:
    return OutboundAcknowledgement(
        outbound_message_id=row.outbound_message_id,
        tenant_id=row.tenant_id,
        order_id=row.order_id,
        acknowledgement_type=row.acknowledgement_type,
        to_number=row.to_number,
        from_number=row.from_number,
        body=row.body,
        status=row.status,
        provider=row.provider,
        provider_message_sid=row.provider_message_sid,
        attempt_count=row.attempt_count,
        last_error_code=row.last_error_code,
        last_error_message=row.last_error_message,
        requested_by=row.requested_by,
        created_at=_utc_aware(row.created_at),
        updated_at=_utc_aware(row.updated_at),
        sent_at=_utc_aware(row.sent_at),
    )


def _suppression_reason(status: str) -> ClaimReason:
    if status in {"send_requested", "sending"}:
        return "suppressed_in_progress"

    if status == "sent":
        return "suppressed_sent"

    if status == "unknown":
        return "suppressed_unknown"

    return "suppressed_existing"


def _require_text(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name} is required")


def _utc_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value
