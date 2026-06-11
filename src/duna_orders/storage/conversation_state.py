from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Protocol

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from duna_orders.domain.models import utc_now
from duna_orders.ids import new_id
from duna_orders.storage.postgres_models import ConversationSessionRow, ConversationTurnRow
from duna_orders.storage.postgres_session import session_scope


ConversationSessionStatus = Literal["open", "draft_created", "expired", "failed"]

ADVANCEMENT_OUTCOME_VALUES = frozenset(
    {
        "TURN_APPENDED_INCOMPLETE",
        "PARSE_INCOMPLETE",
        "DRAFT_CREATED",
        "ALREADY_HAS_DRAFT",
        "DUPLICATE_MESSAGE",
    }
)

PARSE_ERROR_CATEGORY_VALUES = frozenset({"PARSER_ERROR"})


@dataclass(frozen=True)
class ConversationSession:
    conversation_id: str
    tenant_id: str
    customer_phone: str
    status: ConversationSessionStatus
    opened_at: datetime
    last_message_at: datetime
    version: int
    resulting_order_id: str | None
    created_at: datetime
    updated_at: datetime
    latest_advancement_outcome: str | None
    latest_parse_error_category: str | None


@dataclass(frozen=True)
class ConversationTurn:
    turn_id: str
    conversation_id: str
    tenant_id: str
    message_sid: str
    from_number: str | None
    body: str
    received_at: datetime
    sequence_number: int
    created_at: datetime


@dataclass(frozen=True)
class ConversationTurnAppendResult:
    turn: ConversationTurn
    appended: bool


class ConversationStateStore(Protocol):
    def get_or_create_open_session(
        self,
        *,
        tenant_id: str,
        customer_phone: str,
        received_at: datetime,
    ) -> ConversationSession:
        ...

    def append_turn_if_new(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        message_sid: str,
        from_number: str | None,
        body: str,
        received_at: datetime,
    ) -> ConversationTurnAppendResult:
        ...

    def list_turns(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
    ) -> list[ConversationTurn]:
        ...

    def get_session(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
    ) -> ConversationSession | None:
        ...

    def get_latest_session_for_customer(
        self,
        *,
        tenant_id: str,
        customer_phone: str,
    ) -> ConversationSession | None:
        ...

    def mark_draft_created(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        order_id: str,
    ) -> ConversationSession:
        ...

    def record_advancement_attempt(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        outcome: str,
        parse_error_category: str | None = None,
    ) -> ConversationSession:
        ...


class PostgresConversationStateStore:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def get_or_create_open_session(
        self,
        *,
        tenant_id: str,
        customer_phone: str,
        received_at: datetime,
    ) -> ConversationSession:
        _require_text(tenant_id, "tenant_id")
        _require_text(customer_phone, "customer_phone")

        existing = self._get_open_session(
            tenant_id=tenant_id,
            customer_phone=customer_phone,
        )
        if existing is not None:
            return existing

        try:
            return self._try_create_open_session(
                tenant_id=tenant_id,
                customer_phone=customer_phone,
                received_at=received_at,
            )
        except IntegrityError:
            existing = self._get_open_session(
                tenant_id=tenant_id,
                customer_phone=customer_phone,
            )
            if existing is None:
                raise RuntimeError("Open conversation uniqueness conflict without row")

            return existing

    def append_turn_if_new(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        message_sid: str,
        from_number: str | None,
        body: str,
        received_at: datetime,
    ) -> ConversationTurnAppendResult:
        _require_text(tenant_id, "tenant_id")
        _require_text(conversation_id, "conversation_id")
        _require_text(message_sid, "message_sid")

        try:
            return self._try_append_turn(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                message_sid=message_sid,
                from_number=from_number,
                body=body,
                received_at=received_at,
            )
        except IntegrityError:
            existing = self._get_turn_by_message_sid(
                tenant_id=tenant_id,
                message_sid=message_sid,
            )
            if existing is None:
                raise RuntimeError("Conversation turn uniqueness conflict without row")

            return ConversationTurnAppendResult(turn=existing, appended=False)

    def list_turns(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
    ) -> list[ConversationTurn]:
        _require_text(tenant_id, "tenant_id")
        _require_text(conversation_id, "conversation_id")

        with session_scope(self._session_factory) as session:
            rows = session.scalars(
                select(ConversationTurnRow)
                .where(ConversationTurnRow.tenant_id == tenant_id)
                .where(ConversationTurnRow.conversation_id == conversation_id)
                .order_by(
                    ConversationTurnRow.sequence_number,
                    ConversationTurnRow.created_at,
                    ConversationTurnRow.turn_id,
                )
            ).all()

            return [_turn_from_row(row) for row in rows]

    def get_session(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
    ) -> ConversationSession | None:
        _require_text(tenant_id, "tenant_id")
        _require_text(conversation_id, "conversation_id")

        with session_scope(self._session_factory) as session:
            row = session.scalar(
                select(ConversationSessionRow)
                .where(ConversationSessionRow.tenant_id == tenant_id)
                .where(ConversationSessionRow.conversation_id == conversation_id)
            )

            return _session_from_row(row) if row is not None else None

    def get_latest_session_for_customer(
        self,
        *,
        tenant_id: str,
        customer_phone: str,
    ) -> ConversationSession | None:
        _require_text(tenant_id, "tenant_id")
        _require_text(customer_phone, "customer_phone")

        with session_scope(self._session_factory) as session:
            row = session.scalar(
                select(ConversationSessionRow)
                .where(ConversationSessionRow.tenant_id == tenant_id)
                .where(ConversationSessionRow.customer_phone == customer_phone)
                .order_by(
                    ConversationSessionRow.last_message_at.desc(),
                    ConversationSessionRow.updated_at.desc(),
                    ConversationSessionRow.opened_at.desc(),
                    ConversationSessionRow.conversation_id.desc(),
                )
                .limit(1)
            )

            return _session_from_row(row) if row is not None else None

    def mark_draft_created(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        order_id: str,
    ) -> ConversationSession:
        _require_text(tenant_id, "tenant_id")
        _require_text(conversation_id, "conversation_id")
        _require_text(order_id, "order_id")

        with session_scope(self._session_factory) as session:
            row = session.scalar(
                select(ConversationSessionRow)
                .where(ConversationSessionRow.tenant_id == tenant_id)
                .where(ConversationSessionRow.conversation_id == conversation_id)
                .with_for_update()
            )

            if row is None:
                raise ValueError(f"Conversation session not found: {conversation_id}")

            if row.resulting_order_id is not None:
                if row.resulting_order_id != order_id:
                    raise ValueError(
                        "Conversation session already linked to a different order"
                    )
                return _session_from_row(row)

            now = utc_now()
            row.status = "draft_created"
            row.resulting_order_id = order_id
            row.version += 1
            row.updated_at = now
            session.flush()

            return _session_from_row(row)

    def record_advancement_attempt(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        outcome: str,
        parse_error_category: str | None = None,
    ) -> ConversationSession:
        _require_text(tenant_id, "tenant_id")
        _require_text(conversation_id, "conversation_id")

        if outcome not in ADVANCEMENT_OUTCOME_VALUES:
            raise ValueError(f"Unknown advancement outcome: {outcome}")

        if (
            parse_error_category is not None
            and parse_error_category not in PARSE_ERROR_CATEGORY_VALUES
        ):
            raise ValueError(f"Unknown parse error category: {parse_error_category}")

        with session_scope(self._session_factory) as session:
            row = session.scalar(
                select(ConversationSessionRow)
                .where(ConversationSessionRow.tenant_id == tenant_id)
                .where(ConversationSessionRow.conversation_id == conversation_id)
                .with_for_update()
            )

            if row is None:
                raise ValueError(f"Conversation session not found: {conversation_id}")

            row.latest_advancement_outcome = outcome
            row.latest_parse_error_category = parse_error_category
            row.version += 1
            row.updated_at = utc_now()
            session.flush()

            return _session_from_row(row)

    def _get_open_session(
        self,
        *,
        tenant_id: str,
        customer_phone: str,
    ) -> ConversationSession | None:
        with session_scope(self._session_factory) as session:
            row = session.scalar(
                select(ConversationSessionRow)
                .where(ConversationSessionRow.tenant_id == tenant_id)
                .where(ConversationSessionRow.customer_phone == customer_phone)
                .where(ConversationSessionRow.status == "open")
            )

            return _session_from_row(row) if row is not None else None

    def _try_create_open_session(
        self,
        *,
        tenant_id: str,
        customer_phone: str,
        received_at: datetime,
    ) -> ConversationSession:
        session = self._session_factory()

        try:
            now = utc_now()
            row = ConversationSessionRow(
                conversation_id=new_id("conv"),
                tenant_id=tenant_id,
                customer_phone=customer_phone,
                status="open",
                opened_at=received_at,
                last_message_at=received_at,
                version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            return _session_from_row(row)
        except IntegrityError:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _try_append_turn(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        message_sid: str,
        from_number: str | None,
        body: str,
        received_at: datetime,
    ) -> ConversationTurnAppendResult:
        with session_scope(self._session_factory) as session:
            session_row = session.scalar(
                select(ConversationSessionRow)
                .where(ConversationSessionRow.tenant_id == tenant_id)
                .where(ConversationSessionRow.conversation_id == conversation_id)
                .with_for_update()
            )
            if session_row is None:
                raise ValueError(f"Conversation session not found: {conversation_id}")

            next_sequence = (
                session.scalar(
                    select(func.max(ConversationTurnRow.sequence_number))
                    .where(ConversationTurnRow.tenant_id == tenant_id)
                    .where(ConversationTurnRow.conversation_id == conversation_id)
                )
                or 0
            ) + 1
            now = utc_now()
            turn_row = ConversationTurnRow(
                turn_id=new_id("cturn"),
                conversation_id=conversation_id,
                tenant_id=tenant_id,
                message_sid=message_sid,
                from_number=from_number,
                body=body,
                received_at=received_at,
                sequence_number=next_sequence,
                created_at=now,
            )
            session.add(turn_row)
            session.flush()

            session_row.last_message_at = received_at
            session_row.version += 1
            session_row.updated_at = now
            session.flush()

            return ConversationTurnAppendResult(
                turn=_turn_from_row(turn_row),
                appended=True,
            )

    def _get_turn_by_message_sid(
        self,
        *,
        tenant_id: str,
        message_sid: str,
    ) -> ConversationTurn | None:
        with session_scope(self._session_factory) as session:
            row = session.scalar(
                select(ConversationTurnRow)
                .where(ConversationTurnRow.tenant_id == tenant_id)
                .where(ConversationTurnRow.message_sid == message_sid)
            )

            return _turn_from_row(row) if row is not None else None


def _session_from_row(row: ConversationSessionRow) -> ConversationSession:
    return ConversationSession(
        conversation_id=row.conversation_id,
        tenant_id=row.tenant_id,
        customer_phone=row.customer_phone,
        status=row.status,
        opened_at=_utc_aware(row.opened_at),
        last_message_at=_utc_aware(row.last_message_at),
        version=row.version,
        resulting_order_id=row.resulting_order_id,
        created_at=_utc_aware(row.created_at),
        updated_at=_utc_aware(row.updated_at),
        latest_advancement_outcome=row.latest_advancement_outcome,
        latest_parse_error_category=row.latest_parse_error_category,
    )


def _turn_from_row(row: ConversationTurnRow) -> ConversationTurn:
    return ConversationTurn(
        turn_id=row.turn_id,
        conversation_id=row.conversation_id,
        tenant_id=row.tenant_id,
        message_sid=row.message_sid,
        from_number=row.from_number,
        body=row.body,
        received_at=_utc_aware(row.received_at),
        sequence_number=row.sequence_number,
        created_at=_utc_aware(row.created_at),
    )


def _require_text(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name} is required")


def _utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value
