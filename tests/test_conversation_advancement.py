from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.command import upgrade
from alembic.config import Config
from sqlalchemy.exc import IntegrityError

from duna_orders.config import settings
from duna_orders.domain.models import DraftItemRequest, DraftOrderRequest, ParseResult, Product
from duna_orders.parsing.exceptions import ParserError
from duna_orders.services.conversation_advancement import (
    ConversationAdvancementOutcome,
    ConversationAdvancementService,
)
from duna_orders.services.orders import OrderService
from duna_orders.services.parsing import ParsingService
from duna_orders.services.tenant_scoped_reads import TenantScopedReadService
from duna_orders.storage.conversation_orders import PostgresConversationOrderLookup
from duna_orders.storage.conversation_state import PostgresConversationStateStore
from duna_orders.storage.postgres import PostgresStorage
from duna_orders.storage.postgres_base import Base
from duna_orders.storage.postgres_session import make_engine, make_session_factory
from tests._fakes import MockParser
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.test_conversation_state_store import _cleanup_tenant


TENANT_ID = DEFAULT_TEST_TENANT_ID
FROM_NUMBER = "whatsapp:+573001112233"
PRODUCT_ID = "prd_empanada"
BASE_TIME = datetime(2026, 6, 10, 14, 0, tzinfo=timezone.utc)


def _make_product() -> Product:
    return Product(
        tenant_id=TENANT_ID,
        product_id=PRODUCT_ID,
        product_name="Empanada",
        unit_price=Decimal("3000"),
        current_stock=Decimal("100"),
        active=True,
    )


def _complete_parse_result(raw_message: str = "") -> ParseResult:
    return ParseResult(
        request=DraftOrderRequest(
            tenant_id=TENANT_ID,
            raw_message=raw_message,
            customer_name="Cliente Test",
            items=[
                DraftItemRequest(
                    tenant_id=TENANT_ID,
                    product_id=PRODUCT_ID,
                    quantity=Decimal("2"),
                ),
            ],
        ),
        warnings=[],
        model="mock-parser",
        latency_ms=0,
        raw_response="{}",
    )


def _incomplete_parse_result(raw_message: str = "") -> ParseResult:
    return ParseResult(
        request=DraftOrderRequest(
            tenant_id=TENANT_ID,
            raw_message=raw_message,
            customer_name="",
            items=[],
        ),
        warnings=[],
        model="mock-parser",
        latency_ms=0,
        raw_response="{}",
    )


class _SpyTenantScopedReadService(TenantScopedReadService):
    def __init__(self, storage) -> None:
        super().__init__(storage)
        self.list_products_calls: list[dict[str, object]] = []

    def list_products(self, *, tenant_id: str, active_only: bool = True) -> list[Product]:
        self.list_products_calls.append(
            {"tenant_id": tenant_id, "active_only": active_only}
        )
        return super().list_products(tenant_id=tenant_id, active_only=active_only)


class _RaceOrderService(OrderService):
    """Simulates a concurrent unique-conversation_id race.

    The real create_draft is allowed to commit (representing the
    concurrent transaction that "wins"), then this raises IntegrityError
    to represent the calling transaction's own unique-constraint failure.
    """

    def __init__(self, storage) -> None:
        super().__init__(storage)
        self.create_draft_calls = 0

    def create_draft(self, request: DraftOrderRequest):
        self.create_draft_calls += 1
        super().create_draft(request)
        raise IntegrityError(
            "INSERT INTO orders (...) VALUES (...)",
            {},
            Exception("UNIQUE constraint failed: orders.conversation_id"),
        )


class _SpyConversationStateStore:
    """Wraps a real store, recording record_advancement_attempt calls.

    All other methods delegate to the wrapped store unchanged.
    """

    def __init__(
        self,
        store: PostgresConversationStateStore,
        *,
        record_advancement_attempt_error: Exception | None = None,
    ) -> None:
        self._store = store
        self._record_advancement_attempt_error = record_advancement_attempt_error
        self.record_advancement_attempt_calls: list[dict[str, object]] = []

    def __getattr__(self, name: str):
        return getattr(self._store, name)

    def record_advancement_attempt(self, **kwargs):
        self.record_advancement_attempt_calls.append(kwargs)
        if self._record_advancement_attempt_error is not None:
            raise self._record_advancement_attempt_error
        return self._store.record_advancement_attempt(**kwargs)


class Harness:
    def __init__(
        self,
        tmp_path: Path,
        parser: MockParser | None = None,
        *,
        record_advancement_attempt_error: Exception | None = None,
    ) -> None:
        database_path = tmp_path / "conversation_advancement.db"
        engine = make_engine(f"sqlite:///{database_path}")
        Base.metadata.create_all(engine)
        session_factory = make_session_factory(engine)

        self.storage = PostgresStorage(session_factory)
        self.storage.upsert_product(_make_product())

        self.conversation_state_store = _SpyConversationStateStore(
            PostgresConversationStateStore(session_factory),
            record_advancement_attempt_error=record_advancement_attempt_error,
        )
        self.conversation_order_lookup = PostgresConversationOrderLookup(session_factory)
        self.scoped_reads = _SpyTenantScopedReadService(self.storage)
        self.parser = (
            parser if parser is not None else MockParser(result=_complete_parse_result())
        )
        self.parsing_service = ParsingService(self.parser, self.storage)
        self.order_service = OrderService(self.storage)

        self.service = ConversationAdvancementService(
            conversation_state_store=self.conversation_state_store,
            conversation_order_lookup=self.conversation_order_lookup,
            scoped_reads=self.scoped_reads,
            parsing_service=self.parsing_service,
            order_service=self.order_service,
        )


def test_duplicate_message_sid_on_open_session_returns_duplicate_message(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path, parser=MockParser(result=_incomplete_parse_result()))

    first = harness.service.advance(
        tenant_id=TENANT_ID,
        message_sid="SM1",
        from_number=FROM_NUMBER,
        body="hola",
        received_at=BASE_TIME,
    )
    assert first.outcome == ConversationAdvancementOutcome.PARSE_INCOMPLETE
    assert first.turn_appended is True

    second = harness.service.advance(
        tenant_id=TENANT_ID,
        message_sid="SM1",
        from_number=FROM_NUMBER,
        body="hola",
        received_at=BASE_TIME + timedelta(seconds=1),
    )

    assert second.outcome == ConversationAdvancementOutcome.DUPLICATE_MESSAGE
    assert second.turn_appended is False
    assert second.draft_created is False
    assert second.conversation_id == first.conversation_id

    # Duplicate message must not call the parser again or create a draft.
    assert len(harness.parser.calls) == 1
    assert harness.storage.list_orders() == []


def test_incomplete_parse_returns_incomplete_outcome_and_leaves_session_open(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path, parser=MockParser(result=_incomplete_parse_result()))

    result = harness.service.advance(
        tenant_id=TENANT_ID,
        message_sid="SM1",
        from_number=FROM_NUMBER,
        body="hola",
        received_at=BASE_TIME,
    )

    assert result.outcome in (
        ConversationAdvancementOutcome.PARSE_INCOMPLETE,
        ConversationAdvancementOutcome.TURN_APPENDED_INCOMPLETE,
    )
    assert result.draft_created is False
    assert result.resulting_order_id is None

    session = harness.conversation_state_store.get_session(
        tenant_id=TENANT_ID,
        conversation_id=result.conversation_id,
    )
    assert session is not None
    assert session.status == "open"
    assert harness.storage.list_orders() == []


def test_parser_error_returns_turn_appended_incomplete_and_leaves_session_open(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path, parser=MockParser(raise_error=ParserError("boom")))

    result = harness.service.advance(
        tenant_id=TENANT_ID,
        message_sid="SM1",
        from_number=FROM_NUMBER,
        body="???",
        received_at=BASE_TIME,
    )

    assert result.outcome == ConversationAdvancementOutcome.TURN_APPENDED_INCOMPLETE
    assert result.turn_appended is True
    assert result.draft_created is False
    assert result.resulting_order_id is None

    session = harness.conversation_state_store.get_session(
        tenant_id=TENANT_ID,
        conversation_id=result.conversation_id,
    )
    assert session is not None
    assert session.status == "open"
    assert harness.storage.list_orders() == []


def test_complete_transcript_creates_one_draft_with_conversation_id_and_marks_draft_created(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path, parser=MockParser(result=_complete_parse_result()))

    result = harness.service.advance(
        tenant_id=TENANT_ID,
        message_sid="SM1",
        from_number=FROM_NUMBER,
        body="quiero 2 empanadas",
        received_at=BASE_TIME,
    )

    assert result.outcome == ConversationAdvancementOutcome.DRAFT_CREATED
    assert result.turn_appended is True
    assert result.draft_created is True
    assert result.resulting_order_id is not None

    orders = harness.storage.list_orders()
    assert len(orders) == 1
    assert orders[0].order_id == result.resulting_order_id
    assert orders[0].conversation_id == result.conversation_id

    session = harness.conversation_state_store.get_session(
        tenant_id=TENANT_ID,
        conversation_id=result.conversation_id,
    )
    assert session is not None
    assert session.status == "draft_created"
    assert session.resulting_order_id == result.resulting_order_id


def test_post_draft_created_message_appends_turn_and_returns_already_has_draft(
    tmp_path: Path,
) -> None:
    parser = MockParser(result=_complete_parse_result())
    harness = Harness(tmp_path, parser=parser)

    first = harness.service.advance(
        tenant_id=TENANT_ID,
        message_sid="SM1",
        from_number=FROM_NUMBER,
        body="quiero 2 empanadas",
        received_at=BASE_TIME,
    )
    assert first.outcome == ConversationAdvancementOutcome.DRAFT_CREATED

    second = harness.service.advance(
        tenant_id=TENANT_ID,
        message_sid="SM2",
        from_number=FROM_NUMBER,
        body="algo mas?",
        received_at=BASE_TIME + timedelta(minutes=1),
    )

    assert second.outcome == ConversationAdvancementOutcome.ALREADY_HAS_DRAFT
    assert second.turn_appended is True
    assert second.draft_created is False
    assert second.resulting_order_id == first.resulting_order_id
    assert second.conversation_id == first.conversation_id

    # Turn was appended to the existing draft_created session.
    turns = harness.conversation_state_store.list_turns(
        tenant_id=TENANT_ID,
        conversation_id=first.conversation_id,
    )
    assert [turn.message_sid for turn in turns] == ["SM1", "SM2"]

    # No new session was opened for the customer.
    latest = harness.conversation_state_store.get_latest_session_for_customer(
        tenant_id=TENANT_ID,
        customer_phone=FROM_NUMBER,
    )
    assert latest is not None
    assert latest.conversation_id == first.conversation_id

    # No second draft was created, and the parser was not called again.
    assert len(harness.storage.list_orders()) == 1
    assert len(parser.calls) == 1


def test_post_draft_created_duplicate_message_sid_returns_duplicate_message(
    tmp_path: Path,
) -> None:
    parser = MockParser(result=_complete_parse_result())
    harness = Harness(tmp_path, parser=parser)

    first = harness.service.advance(
        tenant_id=TENANT_ID,
        message_sid="SM1",
        from_number=FROM_NUMBER,
        body="quiero 2 empanadas",
        received_at=BASE_TIME,
    )
    assert first.outcome == ConversationAdvancementOutcome.DRAFT_CREATED

    retry = harness.service.advance(
        tenant_id=TENANT_ID,
        message_sid="SM1",
        from_number=FROM_NUMBER,
        body="quiero 2 empanadas",
        received_at=BASE_TIME + timedelta(seconds=1),
    )

    assert retry.outcome == ConversationAdvancementOutcome.DUPLICATE_MESSAGE
    assert retry.turn_appended is False
    assert retry.draft_created is False
    assert retry.resulting_order_id == first.resulting_order_id

    # No second draft, and the parser was not called again.
    assert len(harness.storage.list_orders()) == 1
    assert len(parser.calls) == 1


def test_orphan_draft_crash_window_recovery_marks_existing_draft_and_returns_already_has_draft(
    tmp_path: Path,
) -> None:
    parser = MockParser(result=_complete_parse_result())
    harness = Harness(tmp_path, parser=parser)

    # Simulate the crash window: a turn was appended and a draft order was
    # created with conversation_id, but mark_draft_created was never called.
    session = harness.conversation_state_store.get_or_create_open_session(
        tenant_id=TENANT_ID,
        customer_phone=FROM_NUMBER,
        received_at=BASE_TIME,
    )
    harness.conversation_state_store.append_turn_if_new(
        tenant_id=TENANT_ID,
        conversation_id=session.conversation_id,
        message_sid="SM1",
        from_number=FROM_NUMBER,
        body="quiero 2 empanadas",
        received_at=BASE_TIME,
    )
    orphan_order = harness.order_service.create_draft(
        DraftOrderRequest(
            tenant_id=TENANT_ID,
            raw_message="quiero 2 empanadas",
            customer_name="Cliente Test",
            customer_phone=session.customer_phone,
            conversation_id=session.conversation_id,
            items=[
                DraftItemRequest(
                    tenant_id=TENANT_ID,
                    product_id=PRODUCT_ID,
                    quantity=Decimal("2"),
                ),
            ],
        )
    )

    result = harness.service.advance(
        tenant_id=TENANT_ID,
        message_sid="SM2",
        from_number=FROM_NUMBER,
        body="algo mas?",
        received_at=BASE_TIME + timedelta(minutes=1),
    )

    assert result.outcome == ConversationAdvancementOutcome.ALREADY_HAS_DRAFT
    assert result.turn_appended is True
    assert result.draft_created is False
    assert result.resulting_order_id == orphan_order.order_id
    assert result.conversation_id == session.conversation_id

    recovered_session = harness.conversation_state_store.get_session(
        tenant_id=TENANT_ID,
        conversation_id=session.conversation_id,
    )
    assert recovered_session is not None
    assert recovered_session.status == "draft_created"
    assert recovered_session.resulting_order_id == orphan_order.order_id

    # Recovery must not call the parser or create a second draft.
    assert parser.calls == []
    assert len(harness.storage.list_orders()) == 1


def test_create_draft_integrity_error_recovers_existing_draft_and_returns_already_has_draft(
    tmp_path: Path,
) -> None:
    parser = MockParser(result=_complete_parse_result())
    harness = Harness(tmp_path, parser=parser)

    race_order_service = _RaceOrderService(harness.storage)
    harness.service = ConversationAdvancementService(
        conversation_state_store=harness.conversation_state_store,
        conversation_order_lookup=harness.conversation_order_lookup,
        scoped_reads=harness.scoped_reads,
        parsing_service=harness.parsing_service,
        order_service=race_order_service,
    )

    # No order exists yet for any conversation, so the orphan-draft guard
    # finds nothing before create_draft is attempted.
    assert harness.storage.list_orders() == []

    result = harness.service.advance(
        tenant_id=TENANT_ID,
        message_sid="SM1",
        from_number=FROM_NUMBER,
        body="quiero 2 empanadas",
        received_at=BASE_TIME,
    )

    assert result.outcome == ConversationAdvancementOutcome.ALREADY_HAS_DRAFT
    assert result.turn_appended is True
    assert result.draft_created is False

    # The "concurrent" transaction's commit produced exactly one order, and
    # this call recovered it instead of creating a duplicate.
    orders = harness.storage.list_orders()
    assert len(orders) == 1
    assert orders[0].conversation_id == result.conversation_id
    assert result.resulting_order_id == orders[0].order_id

    session = harness.conversation_state_store.get_session(
        tenant_id=TENANT_ID,
        conversation_id=result.conversation_id,
    )
    assert session is not None
    assert session.status == "draft_created"
    assert session.resulting_order_id == orders[0].order_id

    assert race_order_service.create_draft_calls == 1
    assert len(parser.calls) == 1


def test_advance_uses_tenant_scoped_product_reads(tmp_path: Path) -> None:
    harness = Harness(tmp_path, parser=MockParser(result=_complete_parse_result()))

    harness.service.advance(
        tenant_id=TENANT_ID,
        message_sid="SM1",
        from_number=FROM_NUMBER,
        body="quiero 2 empanadas",
        received_at=BASE_TIME,
    )

    assert harness.scoped_reads.list_products_calls == [
        {"tenant_id": TENANT_ID, "active_only": True}
    ]
    assert harness.parser.calls[0][1] == harness.storage.unscoped_list_products(
        active_only=True
    )


def test_parse_incomplete_records_outcome_with_no_category(tmp_path: Path) -> None:
    harness = Harness(tmp_path, parser=MockParser(result=_incomplete_parse_result()))

    result = harness.service.advance(
        tenant_id=TENANT_ID,
        message_sid="SM1",
        from_number=FROM_NUMBER,
        body="hola",
        received_at=BASE_TIME,
    )

    assert result.outcome == ConversationAdvancementOutcome.PARSE_INCOMPLETE

    session = harness.conversation_state_store.get_session(
        tenant_id=TENANT_ID,
        conversation_id=result.conversation_id,
    )
    assert session is not None
    assert session.latest_advancement_outcome == "PARSE_INCOMPLETE"
    assert session.latest_parse_error_category is None


def test_turn_appended_incomplete_records_outcome_with_parser_error_category(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path, parser=MockParser(raise_error=ParserError("boom")))

    result = harness.service.advance(
        tenant_id=TENANT_ID,
        message_sid="SM1",
        from_number=FROM_NUMBER,
        body="???",
        received_at=BASE_TIME,
    )

    assert result.outcome == ConversationAdvancementOutcome.TURN_APPENDED_INCOMPLETE

    session = harness.conversation_state_store.get_session(
        tenant_id=TENANT_ID,
        conversation_id=result.conversation_id,
    )
    assert session is not None
    assert session.latest_advancement_outcome == "TURN_APPENDED_INCOMPLETE"
    assert session.latest_parse_error_category == "PARSER_ERROR"


def test_draft_created_records_outcome_with_no_category(tmp_path: Path) -> None:
    harness = Harness(tmp_path, parser=MockParser(result=_complete_parse_result()))

    result = harness.service.advance(
        tenant_id=TENANT_ID,
        message_sid="SM1",
        from_number=FROM_NUMBER,
        body="quiero 2 empanadas",
        received_at=BASE_TIME,
    )

    assert result.outcome == ConversationAdvancementOutcome.DRAFT_CREATED

    session = harness.conversation_state_store.get_session(
        tenant_id=TENANT_ID,
        conversation_id=result.conversation_id,
    )
    assert session is not None
    assert session.latest_advancement_outcome == "DRAFT_CREATED"
    assert session.latest_parse_error_category is None
    assert harness.conversation_state_store.record_advancement_attempt_calls == [
        {
            "tenant_id": TENANT_ID,
            "conversation_id": result.conversation_id,
            "outcome": "DRAFT_CREATED",
            "parse_error_category": None,
        }
    ]


def test_already_has_draft_records_outcome_with_no_category(tmp_path: Path) -> None:
    parser = MockParser(result=_complete_parse_result())
    harness = Harness(tmp_path, parser=parser)

    first = harness.service.advance(
        tenant_id=TENANT_ID,
        message_sid="SM1",
        from_number=FROM_NUMBER,
        body="quiero 2 empanadas",
        received_at=BASE_TIME,
    )
    assert first.outcome == ConversationAdvancementOutcome.DRAFT_CREATED

    harness.conversation_state_store.record_advancement_attempt_calls.clear()

    second = harness.service.advance(
        tenant_id=TENANT_ID,
        message_sid="SM2",
        from_number=FROM_NUMBER,
        body="algo mas?",
        received_at=BASE_TIME + timedelta(minutes=1),
    )
    assert second.outcome == ConversationAdvancementOutcome.ALREADY_HAS_DRAFT

    session = harness.conversation_state_store.get_session(
        tenant_id=TENANT_ID,
        conversation_id=second.conversation_id,
    )
    assert session is not None
    assert session.latest_advancement_outcome == "ALREADY_HAS_DRAFT"
    assert session.latest_parse_error_category is None
    assert harness.conversation_state_store.record_advancement_attempt_calls == [
        {
            "tenant_id": TENANT_ID,
            "conversation_id": second.conversation_id,
            "outcome": "ALREADY_HAS_DRAFT",
            "parse_error_category": None,
        }
    ]


def test_orphan_draft_recovery_records_already_has_draft(tmp_path: Path) -> None:
    parser = MockParser(result=_complete_parse_result())
    harness = Harness(tmp_path, parser=parser)

    session = harness.conversation_state_store.get_or_create_open_session(
        tenant_id=TENANT_ID,
        customer_phone=FROM_NUMBER,
        received_at=BASE_TIME,
    )
    harness.conversation_state_store.append_turn_if_new(
        tenant_id=TENANT_ID,
        conversation_id=session.conversation_id,
        message_sid="SM1",
        from_number=FROM_NUMBER,
        body="quiero 2 empanadas",
        received_at=BASE_TIME,
    )
    orphan_order = harness.order_service.create_draft(
        DraftOrderRequest(
            tenant_id=TENANT_ID,
            raw_message="quiero 2 empanadas",
            customer_name="Cliente Test",
            customer_phone=session.customer_phone,
            conversation_id=session.conversation_id,
            items=[
                DraftItemRequest(
                    tenant_id=TENANT_ID,
                    product_id=PRODUCT_ID,
                    quantity=Decimal("2"),
                ),
            ],
        )
    )

    result = harness.service.advance(
        tenant_id=TENANT_ID,
        message_sid="SM2",
        from_number=FROM_NUMBER,
        body="algo mas?",
        received_at=BASE_TIME + timedelta(minutes=1),
    )

    assert result.outcome == ConversationAdvancementOutcome.ALREADY_HAS_DRAFT
    assert result.resulting_order_id == orphan_order.order_id

    recovered_session = harness.conversation_state_store.get_session(
        tenant_id=TENANT_ID,
        conversation_id=session.conversation_id,
    )
    assert recovered_session is not None
    assert recovered_session.latest_advancement_outcome == "ALREADY_HAS_DRAFT"
    assert recovered_session.latest_parse_error_category is None
    assert harness.conversation_state_store.record_advancement_attempt_calls == [
        {
            "tenant_id": TENANT_ID,
            "conversation_id": session.conversation_id,
            "outcome": "ALREADY_HAS_DRAFT",
            "parse_error_category": None,
        }
    ]


def test_create_draft_integrity_error_recovery_records_already_has_draft(
    tmp_path: Path,
) -> None:
    parser = MockParser(result=_complete_parse_result())
    harness = Harness(tmp_path, parser=parser)

    race_order_service = _RaceOrderService(harness.storage)
    harness.service = ConversationAdvancementService(
        conversation_state_store=harness.conversation_state_store,
        conversation_order_lookup=harness.conversation_order_lookup,
        scoped_reads=harness.scoped_reads,
        parsing_service=harness.parsing_service,
        order_service=race_order_service,
    )

    result = harness.service.advance(
        tenant_id=TENANT_ID,
        message_sid="SM1",
        from_number=FROM_NUMBER,
        body="quiero 2 empanadas",
        received_at=BASE_TIME,
    )

    assert result.outcome == ConversationAdvancementOutcome.ALREADY_HAS_DRAFT

    session = harness.conversation_state_store.get_session(
        tenant_id=TENANT_ID,
        conversation_id=result.conversation_id,
    )
    assert session is not None
    assert session.latest_advancement_outcome == "ALREADY_HAS_DRAFT"
    assert session.latest_parse_error_category is None
    assert harness.conversation_state_store.record_advancement_attempt_calls == [
        {
            "tenant_id": TENANT_ID,
            "conversation_id": result.conversation_id,
            "outcome": "ALREADY_HAS_DRAFT",
            "parse_error_category": None,
        }
    ]


def test_duplicate_message_sid_does_not_record(tmp_path: Path) -> None:
    harness = Harness(tmp_path, parser=MockParser(result=_incomplete_parse_result()))

    first = harness.service.advance(
        tenant_id=TENANT_ID,
        message_sid="SM1",
        from_number=FROM_NUMBER,
        body="hola",
        received_at=BASE_TIME,
    )
    assert first.outcome == ConversationAdvancementOutcome.PARSE_INCOMPLETE

    session_after_first = harness.conversation_state_store.get_session(
        tenant_id=TENANT_ID,
        conversation_id=first.conversation_id,
    )
    assert session_after_first is not None

    harness.conversation_state_store.record_advancement_attempt_calls.clear()

    second = harness.service.advance(
        tenant_id=TENANT_ID,
        message_sid="SM1",
        from_number=FROM_NUMBER,
        body="hola",
        received_at=BASE_TIME + timedelta(seconds=1),
    )

    assert second.outcome == ConversationAdvancementOutcome.DUPLICATE_MESSAGE
    assert harness.conversation_state_store.record_advancement_attempt_calls == []

    session_after_second = harness.conversation_state_store.get_session(
        tenant_id=TENANT_ID,
        conversation_id=first.conversation_id,
    )
    assert session_after_second is not None
    assert (
        session_after_second.latest_advancement_outcome
        == session_after_first.latest_advancement_outcome
    )
    assert session_after_second.version == session_after_first.version


def test_recording_failure_is_non_fatal(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    harness = Harness(
        tmp_path,
        parser=MockParser(result=_complete_parse_result()),
        record_advancement_attempt_error=RuntimeError("boom: recording failed"),
    )

    with caplog.at_level(
        logging.WARNING, logger="duna_orders.services.conversation_advancement"
    ):
        result = harness.service.advance(
            tenant_id=TENANT_ID,
            message_sid="SM1",
            from_number=FROM_NUMBER,
            body="quiero 2 empanadas",
            received_at=BASE_TIME,
        )

    assert result.outcome == ConversationAdvancementOutcome.DRAFT_CREATED
    assert result.draft_created is True
    assert result.resulting_order_id is not None

    orders = harness.storage.list_orders()
    assert len(orders) == 1
    assert orders[0].order_id == result.resulting_order_id

    assert any(record.levelname == "WARNING" for record in caplog.records)


def test_parse_error_category_is_cleared_after_subsequent_draft_created(
    tmp_path: Path,
) -> None:
    parser = MockParser(raise_error=ParserError("boom"))
    harness = Harness(tmp_path, parser=parser)

    first = harness.service.advance(
        tenant_id=TENANT_ID,
        message_sid="SM1",
        from_number=FROM_NUMBER,
        body="???",
        received_at=BASE_TIME,
    )
    assert first.outcome == ConversationAdvancementOutcome.TURN_APPENDED_INCOMPLETE

    after_first = harness.conversation_state_store.get_session(
        tenant_id=TENANT_ID,
        conversation_id=first.conversation_id,
    )
    assert after_first is not None
    assert after_first.latest_advancement_outcome == "TURN_APPENDED_INCOMPLETE"
    assert after_first.latest_parse_error_category == "PARSER_ERROR"

    parser._raise_error = None
    parser._result = _complete_parse_result()

    second = harness.service.advance(
        tenant_id=TENANT_ID,
        message_sid="SM2",
        from_number=FROM_NUMBER,
        body="quiero 2 empanadas",
        received_at=BASE_TIME + timedelta(minutes=1),
    )
    assert second.outcome == ConversationAdvancementOutcome.DRAFT_CREATED
    assert second.conversation_id == first.conversation_id

    after_second = harness.conversation_state_store.get_session(
        tenant_id=TENANT_ID,
        conversation_id=second.conversation_id,
    )
    assert after_second is not None
    assert after_second.latest_advancement_outcome == "DRAFT_CREATED"
    assert after_second.latest_parse_error_category is None


@pytest.mark.live_postgres
def test_live_postgres_concurrent_advance_for_same_customer_creates_one_draft() -> None:
    if not settings.database_url:
        pytest.skip("DATABASE_URL is required for live_postgres tests")

    upgrade(Config("alembic.ini"), "head")
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    tenant_id = f"tenant_live_advance_{uuid4().hex}"
    customer_phone = f"whatsapp:+57{uuid4().hex[:10]}"

    try:
        _cleanup_tenant(engine, tenant_id)

        storage = PostgresStorage(session_factory)
        storage.upsert_product(
            Product(
                tenant_id=tenant_id,
                product_id=PRODUCT_ID,
                product_name="Empanada",
                unit_price=Decimal("3000"),
                current_stock=Decimal("100"),
                active=True,
            )
        )

        conversation_state_store = PostgresConversationStateStore(session_factory)
        conversation_order_lookup = PostgresConversationOrderLookup(session_factory)
        scoped_reads = TenantScopedReadService(storage)
        parsing_service = ParsingService(
            MockParser(result=_complete_parse_result()), storage
        )
        order_service = OrderService(storage)

        service = ConversationAdvancementService(
            conversation_state_store=conversation_state_store,
            conversation_order_lookup=conversation_order_lookup,
            scoped_reads=scoped_reads,
            parsing_service=parsing_service,
            order_service=order_service,
        )

        def advance(message_sid: str):
            return service.advance(
                tenant_id=tenant_id,
                message_sid=message_sid,
                from_number=customer_phone,
                body="quiero 2 empanadas",
                received_at=BASE_TIME,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(advance, ["SM_RACE_1", "SM_RACE_2"]))

        outcomes = {result.outcome for result in results}
        assert outcomes <= {
            ConversationAdvancementOutcome.DRAFT_CREATED,
            ConversationAdvancementOutcome.ALREADY_HAS_DRAFT,
        }
        assert ConversationAdvancementOutcome.DRAFT_CREATED in outcomes

        # Both calls must resolve to the same single draft order. The shared
        # live database holds orders for many tenants, so this is checked via
        # the conversation-scoped resulting_order_id rather than
        # storage.list_orders().
        resulting_order_ids = {result.resulting_order_id for result in results}
        assert None not in resulting_order_ids
        assert len(resulting_order_ids) == 1
    finally:
        _cleanup_tenant(engine, tenant_id)
        engine.dispose()
