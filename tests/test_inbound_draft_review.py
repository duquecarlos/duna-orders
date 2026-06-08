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
