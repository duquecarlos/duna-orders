from datetime import datetime, timezone
from decimal import Decimal

from duna_orders.domain.models import Order, OrderItem, ParseLogEntry
from duna_orders.services.inbound_draft_review import InboundDraftReviewService
from duna_orders.storage.memory import InMemoryStorage
from duna_orders.storage.processed_messages import ProcessedMessage
from tests.conftest import DEFAULT_TEST_TENANT_ID


class FakeProcessedMessageReviewStore:
    def __init__(self, messages: list[ProcessedMessage]) -> None:
        self.messages = messages
        self.requested_tenant_ids: list[str] = []

    def list_messages_with_resulting_order(
        self,
        *,
        tenant_id: str,
    ) -> list[ProcessedMessage]:
        self.requested_tenant_ids.append(tenant_id)
        return [message for message in self.messages if message.tenant_id == tenant_id]


def test_list_reviewable_inbound_drafts_returns_only_inbound_linked_drafts_for_tenant() -> None:
    storage = InMemoryStorage()
    linked_draft = _make_order(order_id="ord_linked")
    unlinked_draft = _make_order(order_id="ord_unlinked")
    other_tenant_order = _make_order(order_id="ord_other", tenant_id="tenant_other")
    storage.create_order(linked_draft)
    storage.create_order(unlinked_draft)
    storage.create_order(other_tenant_order)
    message_store = FakeProcessedMessageReviewStore(
        [
            _message(
                message_sid="SM_LINKED",
                resulting_order_id="ord_linked",
                raw_body="Dos empanadas sin ají",
            ),
            _message(
                message_sid="SM_OTHER",
                tenant_id="tenant_other",
                resulting_order_id="ord_other",
            ),
        ]
    )
    service = InboundDraftReviewService(
        storage=storage,
        processed_message_store=message_store,
    )

    review_items = service.list_reviewable_inbound_drafts(
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )

    assert [item.order.order_id for item in review_items] == ["ord_linked"]
    assert review_items[0].message_sid == "SM_LINKED"
    assert review_items[0].raw_inbound_body == "Dos empanadas sin ají"
    assert message_store.requested_tenant_ids == [DEFAULT_TEST_TENANT_ID]


def test_list_reviewable_inbound_drafts_excludes_reviewed_or_confirmed_orders() -> None:
    storage = InMemoryStorage()
    for status in ["approved", "cancelled", "confirmed"]:
        storage.create_order(_make_order(order_id=f"ord_{status}", status=status))
    message_store = FakeProcessedMessageReviewStore(
        [
            _message(message_sid="SM_APPROVED", resulting_order_id="ord_approved"),
            _message(message_sid="SM_CANCELLED", resulting_order_id="ord_cancelled"),
            _message(message_sid="SM_CONFIRMED", resulting_order_id="ord_confirmed"),
        ]
    )
    service = InboundDraftReviewService(
        storage=storage,
        processed_message_store=message_store,
    )

    review_items = service.list_reviewable_inbound_drafts(
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )

    assert review_items == []


def test_list_reviewable_inbound_drafts_includes_raw_body_message_sid_and_from_number() -> None:
    storage = InMemoryStorage()
    storage.create_order(_make_order(order_id="ord_target"))
    message_store = FakeProcessedMessageReviewStore(
        [
            _message(
                message_sid="SM_TARGET",
                resulting_order_id="ord_target",
                from_number="whatsapp:+573001112233",
                raw_body="Una bandeja paisa sin aguacate",
            ),
        ]
    )
    service = InboundDraftReviewService(
        storage=storage,
        processed_message_store=message_store,
    )

    review_items = service.list_reviewable_inbound_drafts(
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )

    assert len(review_items) == 1
    assert review_items[0].message_sid == "SM_TARGET"
    assert review_items[0].raw_inbound_body == "Una bandeja paisa sin aguacate"
    assert review_items[0].from_number == "whatsapp:+573001112233"


def test_list_reviewable_inbound_drafts_normalizes_missing_raw_body_to_empty_string() -> None:
    storage = InMemoryStorage()
    storage.create_order(_make_order(order_id="ord_target"))
    message_store = FakeProcessedMessageReviewStore(
        [
            _message(
                message_sid="SM_TARGET",
                resulting_order_id="ord_target",
                raw_body=None,
            ),
        ]
    )
    service = InboundDraftReviewService(
        storage=storage,
        processed_message_store=message_store,
    )

    review_items = service.list_reviewable_inbound_drafts(
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )

    assert len(review_items) == 1
    assert review_items[0].raw_inbound_body == ""


def test_list_reviewable_inbound_drafts_skips_missing_order_and_returns_valid_drafts() -> None:
    storage = InMemoryStorage()
    storage.create_order(_make_order(order_id="ord_valid"))
    message_store = FakeProcessedMessageReviewStore(
        [
            _message(
                message_sid="SM_MISSING",
                resulting_order_id="ord_missing",
            ),
            _message(
                message_sid="SM_VALID",
                resulting_order_id="ord_valid",
            ),
        ]
    )
    service = InboundDraftReviewService(
        storage=storage,
        processed_message_store=message_store,
    )

    review_items = service.list_reviewable_inbound_drafts(
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )

    assert [item.message_sid for item in review_items] == ["SM_VALID"]
    assert review_items[0].order.order_id == "ord_valid"


def test_list_reviewable_inbound_drafts_does_not_use_parse_log_or_timestamp_matching() -> None:
    storage = InMemoryStorage()
    storage.create_order(_make_order(order_id="ord_parse_only"))
    storage.append_parse_log(
        ParseLogEntry(
            tenant_id=DEFAULT_TEST_TENANT_ID,
            parse_id="prs_target",
            raw_message="Same raw message as draft",
            parsed_json="{}",
            model="test",
            prompt_version="test",
            latency_ms=1,
            success=True,
        )
    )
    message_store = FakeProcessedMessageReviewStore([])
    service = InboundDraftReviewService(
        storage=storage,
        processed_message_store=message_store,
    )

    review_items = service.list_reviewable_inbound_drafts(
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )

    assert review_items == []


def test_list_reviewable_inbound_drafts_preserves_message_store_ordering() -> None:
    storage = InMemoryStorage()
    storage.create_order(_make_order(order_id="ord_new"))
    storage.create_order(_make_order(order_id="ord_old"))
    message_store = FakeProcessedMessageReviewStore(
        [
            _message(message_sid="SM_NEW", resulting_order_id="ord_new"),
            _message(message_sid="SM_OLD", resulting_order_id="ord_old"),
        ]
    )
    service = InboundDraftReviewService(
        storage=storage,
        processed_message_store=message_store,
    )

    review_items = service.list_reviewable_inbound_drafts(
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )

    assert [item.message_sid for item in review_items] == ["SM_NEW", "SM_OLD"]


def test_list_confirmable_approved_orders_returns_only_linked_approved_orders() -> None:
    storage = InMemoryStorage()
    storage.create_order(_make_order(order_id="ord_approved", status="approved"))
    storage.create_order(_make_order(order_id="ord_draft"))
    storage.create_order(_make_order(order_id="ord_unlinked", status="approved"))
    message_store = FakeProcessedMessageReviewStore(
        [
            _message(message_sid="SM_APPROVED", resulting_order_id="ord_approved"),
            _message(message_sid="SM_DRAFT", resulting_order_id="ord_draft"),
        ]
    )
    service = InboundDraftReviewService(
        storage=storage,
        processed_message_store=message_store,
    )

    review_items = service.list_confirmable_approved_orders(
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )

    assert [item.order.order_id for item in review_items] == ["ord_approved"]
    assert review_items[0].message_sid == "SM_APPROVED"


def test_list_confirmable_approved_orders_excludes_confirmed_cancelled_and_drafts() -> None:
    storage = InMemoryStorage()
    for status in ["draft", "confirmed", "cancelled"]:
        storage.create_order(_make_order(order_id=f"ord_{status}", status=status))
    message_store = FakeProcessedMessageReviewStore(
        [
            _message(message_sid="SM_DRAFT", resulting_order_id="ord_draft"),
            _message(message_sid="SM_CONFIRMED", resulting_order_id="ord_confirmed"),
            _message(message_sid="SM_CANCELLED", resulting_order_id="ord_cancelled"),
        ]
    )
    service = InboundDraftReviewService(
        storage=storage,
        processed_message_store=message_store,
    )

    review_items = service.list_confirmable_approved_orders(
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )

    assert review_items == []


def test_list_confirmable_approved_orders_respects_tenant_and_skips_missing_order() -> None:
    storage = InMemoryStorage()
    storage.create_order(_make_order(order_id="ord_valid", status="approved"))
    storage.create_order(
        _make_order(
            order_id="ord_other",
            status="approved",
            tenant_id="tenant_other",
        )
    )
    message_store = FakeProcessedMessageReviewStore(
        [
            _message(message_sid="SM_MISSING", resulting_order_id="ord_missing"),
            _message(message_sid="SM_VALID", resulting_order_id="ord_valid"),
            _message(
                message_sid="SM_OTHER",
                tenant_id="tenant_other",
                resulting_order_id="ord_other",
            ),
        ]
    )
    service = InboundDraftReviewService(
        storage=storage,
        processed_message_store=message_store,
    )

    review_items = service.list_confirmable_approved_orders(
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )

    assert [item.message_sid for item in review_items] == ["SM_VALID"]
    assert review_items[0].order.order_id == "ord_valid"


def test_list_confirmable_approved_orders_includes_raw_body_message_sid_and_from_number() -> None:
    storage = InMemoryStorage()
    storage.create_order(_make_order(order_id="ord_approved", status="approved"))
    message_store = FakeProcessedMessageReviewStore(
        [
            _message(
                message_sid="SM_APPROVED",
                resulting_order_id="ord_approved",
                from_number="whatsapp:+573001112233",
                raw_body="Una bandeja paisa sin aguacate",
            ),
        ]
    )
    service = InboundDraftReviewService(
        storage=storage,
        processed_message_store=message_store,
    )

    review_items = service.list_confirmable_approved_orders(
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )

    assert len(review_items) == 1
    assert review_items[0].message_sid == "SM_APPROVED"
    assert review_items[0].raw_inbound_body == "Una bandeja paisa sin aguacate"
    assert review_items[0].from_number == "whatsapp:+573001112233"


def test_snapshot_counts_missing_linked_order_and_keeps_it_non_actionable() -> None:
    storage = InMemoryStorage()
    message_store = FakeProcessedMessageReviewStore(
        [_message(message_sid="SM_MISSING", resulting_order_id="ord_missing")]
    )
    service = InboundDraftReviewService(
        storage=storage,
        processed_message_store=message_store,
    )

    snapshot = service.get_inbound_review_snapshot(tenant_id=DEFAULT_TEST_TENANT_ID)

    assert snapshot.draft_items == []
    assert snapshot.approved_items == []
    assert snapshot.diagnostics.missing_order_count == 1


def test_snapshot_counts_tenant_mismatched_linked_order_and_keeps_it_non_actionable() -> None:
    storage = InMemoryStorage()
    storage.create_order(
        _make_order(
            order_id="ord_other_tenant",
            tenant_id="tenant_other",
        )
    )
    message_store = FakeProcessedMessageReviewStore(
        [
            _message(
                message_sid="SM_TENANT_MISMATCH",
                resulting_order_id="ord_other_tenant",
            )
        ]
    )
    service = InboundDraftReviewService(
        storage=storage,
        processed_message_store=message_store,
    )

    snapshot = service.get_inbound_review_snapshot(tenant_id=DEFAULT_TEST_TENANT_ID)

    assert snapshot.draft_items == []
    assert snapshot.approved_items == []
    assert snapshot.diagnostics.tenant_mismatch_count == 1


def test_snapshot_counts_confirmed_linked_order_and_keeps_it_non_actionable() -> None:
    storage = InMemoryStorage()
    storage.create_order(_make_order(order_id="ord_confirmed", status="confirmed"))
    message_store = FakeProcessedMessageReviewStore(
        [_message(message_sid="SM_CONFIRMED", resulting_order_id="ord_confirmed")]
    )
    service = InboundDraftReviewService(
        storage=storage,
        processed_message_store=message_store,
    )

    snapshot = service.get_inbound_review_snapshot(tenant_id=DEFAULT_TEST_TENANT_ID)

    assert snapshot.draft_items == []
    assert snapshot.approved_items == []
    assert snapshot.diagnostics.confirmed_count == 1


def test_snapshot_counts_cancelled_linked_order_and_keeps_it_non_actionable() -> None:
    storage = InMemoryStorage()
    storage.create_order(_make_order(order_id="ord_cancelled", status="cancelled"))
    message_store = FakeProcessedMessageReviewStore(
        [_message(message_sid="SM_CANCELLED", resulting_order_id="ord_cancelled")]
    )
    service = InboundDraftReviewService(
        storage=storage,
        processed_message_store=message_store,
    )

    snapshot = service.get_inbound_review_snapshot(tenant_id=DEFAULT_TEST_TENANT_ID)

    assert snapshot.draft_items == []
    assert snapshot.approved_items == []
    assert snapshot.diagnostics.cancelled_count == 1


def test_snapshot_lists_draft_and_approved_linked_orders_in_separate_lists() -> None:
    storage = InMemoryStorage()
    storage.create_order(_make_order(order_id="ord_draft"))
    storage.create_order(_make_order(order_id="ord_approved", status="approved"))
    message_store = FakeProcessedMessageReviewStore(
        [
            _message(message_sid="SM_DRAFT", resulting_order_id="ord_draft"),
            _message(message_sid="SM_APPROVED", resulting_order_id="ord_approved"),
        ]
    )
    service = InboundDraftReviewService(
        storage=storage,
        processed_message_store=message_store,
    )

    snapshot = service.get_inbound_review_snapshot(tenant_id=DEFAULT_TEST_TENANT_ID)

    assert [item.message_sid for item in snapshot.draft_items] == ["SM_DRAFT"]
    assert [item.message_sid for item in snapshot.approved_items] == ["SM_APPROVED"]
    assert snapshot.diagnostics.draft_count == 1
    assert snapshot.diagnostics.approved_count == 1
    assert snapshot.diagnostics.skipped_count == 0


def test_snapshot_ignores_unlinked_processed_messages_for_diagnostics() -> None:
    storage = InMemoryStorage()
    message_store = FakeProcessedMessageReviewStore(
        [_message(message_sid="SM_UNLINKED", resulting_order_id=None)]
    )
    service = InboundDraftReviewService(
        storage=storage,
        processed_message_store=message_store,
    )

    snapshot = service.get_inbound_review_snapshot(tenant_id=DEFAULT_TEST_TENANT_ID)

    assert snapshot.draft_items == []
    assert snapshot.approved_items == []
    assert snapshot.diagnostics.skipped_count == 0


def test_snapshot_does_not_use_parse_log_or_timestamp_matching() -> None:
    storage = InMemoryStorage()
    storage.create_order(_make_order(order_id="ord_parse_only"))
    storage.append_parse_log(
        ParseLogEntry(
            tenant_id=DEFAULT_TEST_TENANT_ID,
            parse_id="prs_target",
            raw_message="Same raw message as draft",
            parsed_json="{}",
            model="test",
            prompt_version="test",
            latency_ms=1,
            success=True,
        )
    )
    message_store = FakeProcessedMessageReviewStore([])
    service = InboundDraftReviewService(
        storage=storage,
        processed_message_store=message_store,
    )

    snapshot = service.get_inbound_review_snapshot(tenant_id=DEFAULT_TEST_TENANT_ID)

    assert snapshot.draft_items == []
    assert snapshot.approved_items == []
    assert snapshot.diagnostics.skipped_count == 0


def _make_order(
    *,
    order_id: str,
    tenant_id: str = DEFAULT_TEST_TENANT_ID,
    status: str = "draft",
) -> Order:
    item = OrderItem(
        tenant_id=tenant_id,
        order_item_id=f"oit_{order_id}",
        order_id=order_id,
        product_id="prd_empanada",
        product_name_snapshot="Empanada",
        quantity=Decimal("2"),
        unit_price_snapshot=Decimal("3000"),
        line_total=Decimal("6000"),
        modifications="sin ají",
        validation_status="ok",
    )
    return Order(
        tenant_id=tenant_id,
        order_id=order_id,
        raw_message="Dos empanadas sin ají",
        status=status,
        items=[item],
        subtotal=Decimal("6000"),
        total=Decimal("6000"),
    )


def _message(
    *,
    message_sid: str,
    resulting_order_id: str | None,
    tenant_id: str = DEFAULT_TEST_TENANT_ID,
    from_number: str | None = None,
    raw_body: str | None = "Raw inbound",
) -> ProcessedMessage:
    return ProcessedMessage(
        message_sid=message_sid,
        tenant_id=tenant_id,
        received_at=datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc),
        from_number=from_number,
        raw_body=raw_body,
        resulting_order_id=resulting_order_id,
    )
