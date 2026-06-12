from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from sqlalchemy.exc import IntegrityError

from duna_orders.domain.models import DraftOrderRequest, Product
from duna_orders.parsing.exceptions import ParserError
from duna_orders.services.orders import OrderService
from duna_orders.services.parsing import ParsingService
from duna_orders.services.tenant_scoped_reads import TenantScopedReadService
from duna_orders.storage.conversation_orders import ConversationOrderLookup
from duna_orders.storage.conversation_state import (
    ConversationSession,
    ConversationStateStore,
    ConversationTurn,
)


logger = logging.getLogger(__name__)


REACHABLE_SESSION_STATUSES = ("open", "draft_created")


class ConversationAdvancementOutcome(str, Enum):
    TURN_APPENDED_INCOMPLETE = "TURN_APPENDED_INCOMPLETE"
    PARSE_INCOMPLETE = "PARSE_INCOMPLETE"
    DRAFT_CREATED = "DRAFT_CREATED"
    ALREADY_HAS_DRAFT = "ALREADY_HAS_DRAFT"
    DUPLICATE_MESSAGE = "DUPLICATE_MESSAGE"


@dataclass(frozen=True)
class ConversationAdvancementResult:
    outcome: ConversationAdvancementOutcome
    conversation_id: str
    turn_appended: bool
    draft_created: bool
    resulting_order_id: str | None
    parse_error_category: str | None = None


class ConversationAdvancementService:
    def __init__(
        self,
        *,
        conversation_state_store: ConversationStateStore,
        conversation_order_lookup: ConversationOrderLookup,
        scoped_reads: TenantScopedReadService,
        parsing_service: ParsingService,
        order_service: OrderService,
    ) -> None:
        self._conversation_state_store = conversation_state_store
        self._conversation_order_lookup = conversation_order_lookup
        self._scoped_reads = scoped_reads
        self._parsing_service = parsing_service
        self._order_service = order_service

    def advance(
        self,
        *,
        tenant_id: str,
        message_sid: str,
        from_number: str,
        body: str,
        received_at: datetime,
        renew_customer_claim: Callable[[], bool] | None = None,
    ) -> ConversationAdvancementResult:
        _require_text(tenant_id, "tenant_id")
        _require_text(message_sid, "message_sid")
        _require_text(from_number, "from_number")

        session = self._route_session(
            tenant_id=tenant_id,
            customer_phone=from_number,
            received_at=received_at,
        )

        append_result = self._conversation_state_store.append_turn_if_new(
            tenant_id=tenant_id,
            conversation_id=session.conversation_id,
            message_sid=message_sid,
            from_number=from_number,
            body=body,
            received_at=received_at,
        )
        turn_appended = append_result.appended

        if session.status == "draft_created":
            outcome = (
                ConversationAdvancementOutcome.ALREADY_HAS_DRAFT
                if turn_appended
                else ConversationAdvancementOutcome.DUPLICATE_MESSAGE
            )
            result = ConversationAdvancementResult(
                outcome=outcome,
                conversation_id=session.conversation_id,
                turn_appended=turn_appended,
                draft_created=False,
                resulting_order_id=session.resulting_order_id,
            )
        else:
            orphan_recovery = self._recover_orphan_draft(
                tenant_id=tenant_id,
                session=session,
                turn_appended=turn_appended,
            )
            if orphan_recovery is not None:
                result = orphan_recovery
            elif not turn_appended:
                result = ConversationAdvancementResult(
                    outcome=ConversationAdvancementOutcome.DUPLICATE_MESSAGE,
                    conversation_id=session.conversation_id,
                    turn_appended=False,
                    draft_created=False,
                    resulting_order_id=None,
                )
            else:
                result = self._advance_open_session(
                    tenant_id=tenant_id,
                    session=session,
                    renew_customer_claim=renew_customer_claim,
                )

        if result.outcome == ConversationAdvancementOutcome.DUPLICATE_MESSAGE:
            return result

        return self._record_outcome(
            tenant_id=tenant_id,
            result=result,
            parse_error_category=result.parse_error_category,
        )

    def _record_outcome(
        self,
        *,
        tenant_id: str,
        result: ConversationAdvancementResult,
        parse_error_category: str | None = None,
    ) -> ConversationAdvancementResult:
        try:
            self._conversation_state_store.record_advancement_attempt(
                tenant_id=tenant_id,
                conversation_id=result.conversation_id,
                outcome=result.outcome.value,
                parse_error_category=parse_error_category,
            )
        except Exception:
            logger.warning(
                "Failed to record conversation advancement outcome for "
                "conversation_id=%s outcome=%s",
                result.conversation_id,
                result.outcome.value,
                exc_info=True,
            )

        return result

    def _route_session(
        self,
        *,
        tenant_id: str,
        customer_phone: str,
        received_at: datetime,
    ) -> ConversationSession:
        latest = self._conversation_state_store.get_latest_session_for_customer(
            tenant_id=tenant_id,
            customer_phone=customer_phone,
        )

        if latest is None:
            return self._conversation_state_store.get_or_create_open_session(
                tenant_id=tenant_id,
                customer_phone=customer_phone,
                received_at=received_at,
            )

        if latest.status not in REACHABLE_SESSION_STATUSES:
            raise NotImplementedError(
                "ConversationAdvancementService has no routing policy for "
                f"conversation session status {latest.status!r}; only "
                f"{REACHABLE_SESSION_STATUSES} are reachable today."
            )

        return latest

    def _recover_orphan_draft(
        self,
        *,
        tenant_id: str,
        session: ConversationSession,
        turn_appended: bool,
    ) -> ConversationAdvancementResult | None:
        if session.resulting_order_id is not None:
            return ConversationAdvancementResult(
                outcome=ConversationAdvancementOutcome.ALREADY_HAS_DRAFT,
                conversation_id=session.conversation_id,
                turn_appended=turn_appended,
                draft_created=False,
                resulting_order_id=session.resulting_order_id,
            )

        existing_order = self._conversation_order_lookup.get_order_by_conversation_id(
            tenant_id=tenant_id,
            conversation_id=session.conversation_id,
        )
        if existing_order is None:
            return None

        marked = self._conversation_state_store.mark_draft_created(
            tenant_id=tenant_id,
            conversation_id=session.conversation_id,
            order_id=existing_order.order_id,
        )
        return ConversationAdvancementResult(
            outcome=ConversationAdvancementOutcome.ALREADY_HAS_DRAFT,
            conversation_id=session.conversation_id,
            turn_appended=turn_appended,
            draft_created=False,
            resulting_order_id=marked.resulting_order_id,
        )

    def _advance_open_session(
        self,
        *,
        tenant_id: str,
        session: ConversationSession,
        renew_customer_claim: Callable[[], bool] | None = None,
    ) -> ConversationAdvancementResult:
        turns = self._conversation_state_store.list_turns(
            tenant_id=tenant_id,
            conversation_id=session.conversation_id,
        )
        transcript = _render_transcript(turns)
        products = self._scoped_reads.list_products(tenant_id=tenant_id, active_only=True)

        try:
            parse_result = self._parsing_service.parse(
                tenant_id=tenant_id,
                raw_message=transcript,
                products=products,
            )
        except ParserError:
            return ConversationAdvancementResult(
                outcome=ConversationAdvancementOutcome.TURN_APPENDED_INCOMPLETE,
                conversation_id=session.conversation_id,
                turn_appended=True,
                draft_created=False,
                resulting_order_id=None,
                parse_error_category="PARSER_ERROR",
            )

        # The parser/LLM call has no upper bound on duration. Renew the
        # customer claim now, before any draft/session write, so a lease
        # that expired during parsing aborts the write phase instead of
        # writing under a claim another holder may already own.
        if renew_customer_claim is not None and not renew_customer_claim():
            logger.warning(
                "Customer claim lost while advancing conversation_id=%s; "
                "aborting write phase",
                session.conversation_id,
            )
            return ConversationAdvancementResult(
                outcome=ConversationAdvancementOutcome.TURN_APPENDED_INCOMPLETE,
                conversation_id=session.conversation_id,
                turn_appended=True,
                draft_created=False,
                resulting_order_id=None,
            )

        request = parse_result.request

        if not _is_complete(request, products):
            return ConversationAdvancementResult(
                outcome=ConversationAdvancementOutcome.PARSE_INCOMPLETE,
                conversation_id=session.conversation_id,
                turn_appended=True,
                draft_created=False,
                resulting_order_id=None,
            )

        revalidation = self._recover_from_create_draft_conflict(
            tenant_id=tenant_id,
            session=session,
        )
        if revalidation is not None:
            return revalidation

        request = request.model_copy(
            update={
                "tenant_id": tenant_id,
                "conversation_id": session.conversation_id,
                "raw_message": transcript,
                "customer_phone": session.customer_phone,
                "items": [
                    item.model_copy(update={"tenant_id": tenant_id})
                    for item in request.items
                ],
            },
        )

        try:
            order = self._order_service.create_draft(request)
        except IntegrityError:
            recovered = self._recover_from_create_draft_conflict(
                tenant_id=tenant_id,
                session=session,
            )
            if recovered is None:
                raise
            return recovered

        marked = self._conversation_state_store.mark_draft_created(
            tenant_id=tenant_id,
            conversation_id=session.conversation_id,
            order_id=order.order_id,
        )
        return ConversationAdvancementResult(
            outcome=ConversationAdvancementOutcome.DRAFT_CREATED,
            conversation_id=session.conversation_id,
            turn_appended=True,
            draft_created=True,
            resulting_order_id=marked.resulting_order_id,
        )

    def _recover_from_create_draft_conflict(
        self,
        *,
        tenant_id: str,
        session: ConversationSession,
    ) -> ConversationAdvancementResult | None:
        existing_order = self._conversation_order_lookup.get_order_by_conversation_id(
            tenant_id=tenant_id,
            conversation_id=session.conversation_id,
        )
        if existing_order is None:
            return None

        marked = self._conversation_state_store.mark_draft_created(
            tenant_id=tenant_id,
            conversation_id=session.conversation_id,
            order_id=existing_order.order_id,
        )
        return ConversationAdvancementResult(
            outcome=ConversationAdvancementOutcome.ALREADY_HAS_DRAFT,
            conversation_id=session.conversation_id,
            turn_appended=True,
            draft_created=False,
            resulting_order_id=marked.resulting_order_id,
        )


def _render_transcript(turns: list[ConversationTurn]) -> str:
    return "\n\n".join(
        f"Customer message {index}:\n{turn.body}"
        for index, turn in enumerate(turns, start=1)
    )


def _is_complete(request: DraftOrderRequest, products: list[Product]) -> bool:
    if not request.items:
        return False

    product_ids = {product.product_id for product in products}

    for item in request.items:
        if not item.product_id:
            return False
        if item.quantity <= 0:
            return False
        if item.product_id not in product_ids:
            return False

    return True


def _require_text(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name} is required")
