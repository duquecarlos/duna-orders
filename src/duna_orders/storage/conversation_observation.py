from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from duna_orders.storage.conversation_state import ConversationSessionStatus
from duna_orders.storage.postgres_models import ConversationSessionRow, ConversationTurnRow
from duna_orders.storage.postgres_session import session_scope


ATTENTION_TURN_THRESHOLD = 3
LATEST_BODY_PREVIEW_LENGTH = 160
DEFAULT_IDLE_THRESHOLD = timedelta(hours=4)


@dataclass(frozen=True)
class ConversationObservationItem:
    conversation_id: str
    tenant_id: str
    customer_phone: str
    status: ConversationSessionStatus
    opened_at: datetime
    last_message_at: datetime
    updated_at: datetime
    version: int
    turn_count: int
    latest_message_sid: str | None
    latest_body_preview: str | None
    linked_order_id: str | None
    has_draft: bool
    is_idle: bool
    needs_operator_attention: bool
    latest_advancement_outcome: str | None
    latest_parse_error_category: str | None


@dataclass(frozen=True)
class ConversationObservationDiagnostics:
    total_count: int
    open_count: int
    draft_created_count: int
    idle_count: int
    needs_attention_count: int


@dataclass(frozen=True)
class ConversationObservationSnapshot:
    items: list[ConversationObservationItem]
    diagnostics: ConversationObservationDiagnostics


class ConversationObservationReads(Protocol):
    def get_conversation_observation_snapshot(
        self,
        *,
        tenant_id: str,
        now: datetime,
        idle_threshold: timedelta = DEFAULT_IDLE_THRESHOLD,
    ) -> ConversationObservationSnapshot:
        ...


class PostgresConversationObservationReads:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def get_conversation_observation_snapshot(
        self,
        *,
        tenant_id: str,
        now: datetime,
        idle_threshold: timedelta = DEFAULT_IDLE_THRESHOLD,
    ) -> ConversationObservationSnapshot:
        _require_text(tenant_id, "tenant_id")

        with session_scope(self._session_factory) as session:
            session_rows = session.scalars(
                select(ConversationSessionRow)
                .where(ConversationSessionRow.tenant_id == tenant_id)
                .order_by(
                    ConversationSessionRow.last_message_at.desc(),
                    ConversationSessionRow.updated_at.desc(),
                    ConversationSessionRow.opened_at.desc(),
                    ConversationSessionRow.conversation_id.desc(),
                )
            ).all()

            turn_counts = dict(
                session.execute(
                    select(
                        ConversationTurnRow.conversation_id,
                        func.count(ConversationTurnRow.turn_id),
                    )
                    .where(ConversationTurnRow.tenant_id == tenant_id)
                    .group_by(ConversationTurnRow.conversation_id)
                ).all()
            )

            other_turn = aliased(ConversationTurnRow)
            latest_turns = {
                row.conversation_id: row
                for row in session.execute(
                    select(
                        ConversationTurnRow.conversation_id,
                        ConversationTurnRow.message_sid,
                        ConversationTurnRow.body,
                    )
                    .where(ConversationTurnRow.tenant_id == tenant_id)
                    .where(
                        ConversationTurnRow.sequence_number
                        == (
                            select(func.max(other_turn.sequence_number))
                            .where(other_turn.tenant_id == tenant_id)
                            .where(
                                other_turn.conversation_id
                                == ConversationTurnRow.conversation_id
                            )
                            .scalar_subquery()
                        )
                    )
                ).all()
            }

            items = [
                _item_from_row(
                    row,
                    turn_count=turn_counts.get(row.conversation_id, 0),
                    latest_turn=latest_turns.get(row.conversation_id),
                    now=now,
                    idle_threshold=idle_threshold,
                )
                for row in session_rows
            ]

            return ConversationObservationSnapshot(
                items=items,
                diagnostics=_diagnostics_from_items(items),
            )


def _item_from_row(
    row: ConversationSessionRow,
    *,
    turn_count: int,
    latest_turn,
    now: datetime,
    idle_threshold: timedelta,
) -> ConversationObservationItem:
    last_message_at = _utc_aware(row.last_message_at)
    is_idle = (now - last_message_at) > idle_threshold
    linked_order_id = row.resulting_order_id

    if latest_turn is None:
        latest_message_sid = None
        latest_body_preview = None
    else:
        latest_message_sid = latest_turn.message_sid
        latest_body_preview = latest_turn.body[:LATEST_BODY_PREVIEW_LENGTH]

    return ConversationObservationItem(
        conversation_id=row.conversation_id,
        tenant_id=row.tenant_id,
        customer_phone=row.customer_phone,
        status=row.status,
        opened_at=_utc_aware(row.opened_at),
        last_message_at=last_message_at,
        updated_at=_utc_aware(row.updated_at),
        version=row.version,
        turn_count=turn_count,
        latest_message_sid=latest_message_sid,
        latest_body_preview=latest_body_preview,
        linked_order_id=linked_order_id,
        has_draft=linked_order_id is not None,
        is_idle=is_idle,
        needs_operator_attention=(
            row.status == "open"
            and linked_order_id is None
            and (turn_count >= ATTENTION_TURN_THRESHOLD or is_idle)
        ),
        latest_advancement_outcome=row.latest_advancement_outcome,
        latest_parse_error_category=row.latest_parse_error_category,
    )


def _diagnostics_from_items(
    items: list[ConversationObservationItem],
) -> ConversationObservationDiagnostics:
    return ConversationObservationDiagnostics(
        total_count=len(items),
        open_count=sum(1 for item in items if item.status == "open"),
        draft_created_count=sum(1 for item in items if item.status == "draft_created"),
        idle_count=sum(1 for item in items if item.is_idle),
        needs_attention_count=sum(1 for item in items if item.needs_operator_attention),
    )


def _require_text(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name} is required")


def _utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value
