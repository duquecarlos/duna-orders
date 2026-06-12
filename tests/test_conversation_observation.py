from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from duna_orders.storage.base import StorageInterface
from duna_orders.storage.conversation_observation import (
    ATTENTION_TURN_THRESHOLD,
    LATEST_BODY_PREVIEW_LENGTH,
    ConversationTurnObservationItem,
    PostgresConversationObservationReads,
)
from duna_orders.storage.conversation_state import PostgresConversationStateStore
from duna_orders.storage.postgres_base import Base
from duna_orders.storage.postgres_session import make_engine, make_session_factory


TENANT_A = "tenant_observation_a"
TENANT_B = "tenant_observation_b"
CUSTOMER_PHONE = "whatsapp:+573001112233"
FROM_NUMBER = "whatsapp:+573001112233"
BASE_TIME = datetime(2026, 6, 10, 14, 0, tzinfo=timezone.utc)


def _stores(
    tmp_path: Path,
) -> tuple[PostgresConversationStateStore, PostgresConversationObservationReads]:
    database_path = tmp_path / "conversation_observation.db"
    engine = make_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    return (
        PostgresConversationStateStore(session_factory),
        PostgresConversationObservationReads(session_factory),
    )


def test_empty_tenant_snapshot_returns_no_items_and_zero_diagnostics(
    tmp_path: Path,
) -> None:
    _, reads = _stores(tmp_path)

    snapshot = reads.get_conversation_observation_snapshot(
        tenant_id=TENANT_A,
        now=BASE_TIME,
    )

    assert snapshot.items == []
    assert snapshot.diagnostics.total_count == 0
    assert snapshot.diagnostics.open_count == 0
    assert snapshot.diagnostics.draft_created_count == 0
    assert snapshot.diagnostics.idle_count == 0
    assert snapshot.diagnostics.needs_attention_count == 0


def test_snapshot_is_tenant_scoped(tmp_path: Path) -> None:
    store, reads = _stores(tmp_path)

    session_a = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )
    store.get_or_create_open_session(
        tenant_id=TENANT_B,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )

    snapshot = reads.get_conversation_observation_snapshot(tenant_id=TENANT_A, now=BASE_TIME)

    assert [item.conversation_id for item in snapshot.items] == [session_a.conversation_id]
    assert all(item.tenant_id == TENANT_A for item in snapshot.items)


def test_session_with_zero_turns_is_included(tmp_path: Path) -> None:
    store, reads = _stores(tmp_path)

    with_turn = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone="whatsapp:+573000000001",
        received_at=BASE_TIME,
    )
    store.append_turn_if_new(
        tenant_id=TENANT_A,
        conversation_id=with_turn.conversation_id,
        message_sid="SM_ZERO_TURNS_SIBLING",
        from_number=FROM_NUMBER,
        body="hola",
        received_at=BASE_TIME,
    )

    without_turn = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone="whatsapp:+573000000002",
        received_at=BASE_TIME,
    )

    snapshot = reads.get_conversation_observation_snapshot(tenant_id=TENANT_A, now=BASE_TIME)
    items_by_id = {item.conversation_id: item for item in snapshot.items}

    assert set(items_by_id) == {with_turn.conversation_id, without_turn.conversation_id}
    assert items_by_id[without_turn.conversation_id].turn_count == 0


def test_turn_count_matches_list_turns_length(tmp_path: Path) -> None:
    store, reads = _stores(tmp_path)
    session = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )

    for index in range(3):
        store.append_turn_if_new(
            tenant_id=TENANT_A,
            conversation_id=session.conversation_id,
            message_sid=f"SM_TURN_COUNT_{index}",
            from_number=FROM_NUMBER,
            body=f"mensaje {index}",
            received_at=BASE_TIME + timedelta(minutes=index),
        )

    snapshot = reads.get_conversation_observation_snapshot(tenant_id=TENANT_A, now=BASE_TIME)
    turns = store.list_turns(tenant_id=TENANT_A, conversation_id=session.conversation_id)

    assert snapshot.items[0].turn_count == len(turns) == 3


def test_latest_message_sid_comes_from_highest_sequence_number(tmp_path: Path) -> None:
    store, reads = _stores(tmp_path)
    session = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )

    for index in range(3):
        store.append_turn_if_new(
            tenant_id=TENANT_A,
            conversation_id=session.conversation_id,
            message_sid=f"SM_SEQ_{index}",
            from_number=FROM_NUMBER,
            body=f"mensaje {index}",
            received_at=BASE_TIME + timedelta(minutes=index),
        )

    snapshot = reads.get_conversation_observation_snapshot(tenant_id=TENANT_A, now=BASE_TIME)

    assert snapshot.items[0].latest_message_sid == "SM_SEQ_2"


def test_latest_body_preview_truncates_long_body(tmp_path: Path) -> None:
    store, reads = _stores(tmp_path)
    session = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )
    long_body = "x" * (LATEST_BODY_PREVIEW_LENGTH + 50)

    store.append_turn_if_new(
        tenant_id=TENANT_A,
        conversation_id=session.conversation_id,
        message_sid="SM_LONG_BODY",
        from_number=FROM_NUMBER,
        body=long_body,
        received_at=BASE_TIME,
    )

    snapshot = reads.get_conversation_observation_snapshot(tenant_id=TENANT_A, now=BASE_TIME)

    preview = snapshot.items[0].latest_body_preview
    assert preview == long_body[:LATEST_BODY_PREVIEW_LENGTH]
    assert len(preview) == LATEST_BODY_PREVIEW_LENGTH


def test_latest_body_preview_preserves_short_body(tmp_path: Path) -> None:
    store, reads = _stores(tmp_path)
    session = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )

    store.append_turn_if_new(
        tenant_id=TENANT_A,
        conversation_id=session.conversation_id,
        message_sid="SM_SHORT_BODY",
        from_number=FROM_NUMBER,
        body="hola",
        received_at=BASE_TIME,
    )

    snapshot = reads.get_conversation_observation_snapshot(tenant_id=TENANT_A, now=BASE_TIME)

    assert snapshot.items[0].latest_body_preview == "hola"


def test_latest_body_preview_preserves_empty_string_body(tmp_path: Path) -> None:
    store, reads = _stores(tmp_path)
    session = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )

    store.append_turn_if_new(
        tenant_id=TENANT_A,
        conversation_id=session.conversation_id,
        message_sid="SM_EMPTY_BODY",
        from_number=FROM_NUMBER,
        body="",
        received_at=BASE_TIME,
    )

    snapshot = reads.get_conversation_observation_snapshot(tenant_id=TENANT_A, now=BASE_TIME)

    item = snapshot.items[0]
    assert item.latest_message_sid == "SM_EMPTY_BODY"
    assert item.latest_body_preview == ""


def test_no_turns_gives_none_latest_message_sid_and_preview(tmp_path: Path) -> None:
    store, reads = _stores(tmp_path)
    store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )

    snapshot = reads.get_conversation_observation_snapshot(tenant_id=TENANT_A, now=BASE_TIME)

    item = snapshot.items[0]
    assert item.turn_count == 0
    assert item.latest_message_sid is None
    assert item.latest_body_preview is None


def test_has_draft_and_linked_order_id_reflect_mark_draft_created(tmp_path: Path) -> None:
    store, reads = _stores(tmp_path)

    with_draft = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone="whatsapp:+573000000003",
        received_at=BASE_TIME,
    )
    store.append_turn_if_new(
        tenant_id=TENANT_A,
        conversation_id=with_draft.conversation_id,
        message_sid="SM_DRAFT_LINK",
        from_number=FROM_NUMBER,
        body="2 empanadas",
        received_at=BASE_TIME,
    )
    store.mark_draft_created(
        tenant_id=TENANT_A,
        conversation_id=with_draft.conversation_id,
        order_id="ord_observation_draft",
    )

    without_draft = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone="whatsapp:+573000000004",
        received_at=BASE_TIME,
    )

    snapshot = reads.get_conversation_observation_snapshot(tenant_id=TENANT_A, now=BASE_TIME)
    items_by_id = {item.conversation_id: item for item in snapshot.items}

    with_draft_item = items_by_id[with_draft.conversation_id]
    without_draft_item = items_by_id[without_draft.conversation_id]

    assert with_draft_item.has_draft is True
    assert with_draft_item.linked_order_id == "ord_observation_draft"
    assert with_draft_item.status == "draft_created"

    assert without_draft_item.has_draft is False
    assert without_draft_item.linked_order_id is None


def test_snapshot_exposes_latest_advancement_outcome_and_parse_error_category(
    tmp_path: Path,
) -> None:
    store, reads = _stores(tmp_path)

    untouched = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone="whatsapp:+573000000005",
        received_at=BASE_TIME,
    )

    recorded = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone="whatsapp:+573000000006",
        received_at=BASE_TIME,
    )
    store.record_advancement_attempt(
        tenant_id=TENANT_A,
        conversation_id=recorded.conversation_id,
        outcome="TURN_APPENDED_INCOMPLETE",
        parse_error_category="PARSER_ERROR",
    )

    snapshot = reads.get_conversation_observation_snapshot(tenant_id=TENANT_A, now=BASE_TIME)
    items_by_id = {item.conversation_id: item for item in snapshot.items}

    untouched_item = items_by_id[untouched.conversation_id]
    recorded_item = items_by_id[recorded.conversation_id]

    assert untouched_item.latest_advancement_outcome is None
    assert untouched_item.latest_parse_error_category is None
    assert recorded_item.latest_advancement_outcome == "TURN_APPENDED_INCOMPLETE"
    assert recorded_item.latest_parse_error_category == "PARSER_ERROR"


def test_is_idle_based_on_now_and_idle_threshold(tmp_path: Path) -> None:
    store, reads = _stores(tmp_path)
    store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )

    not_idle_snapshot = reads.get_conversation_observation_snapshot(
        tenant_id=TENANT_A,
        now=BASE_TIME + timedelta(hours=2),
        idle_threshold=timedelta(hours=4),
    )
    idle_snapshot = reads.get_conversation_observation_snapshot(
        tenant_id=TENANT_A,
        now=BASE_TIME + timedelta(hours=5),
        idle_threshold=timedelta(hours=4),
    )

    assert not_idle_snapshot.items[0].is_idle is False
    assert idle_snapshot.items[0].is_idle is True


def test_needs_operator_attention_true_via_turn_count_branch(tmp_path: Path) -> None:
    store, reads = _stores(tmp_path)
    session = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )

    for index in range(ATTENTION_TURN_THRESHOLD):
        store.append_turn_if_new(
            tenant_id=TENANT_A,
            conversation_id=session.conversation_id,
            message_sid=f"SM_ATTENTION_TURNS_{index}",
            from_number=FROM_NUMBER,
            body=f"mensaje {index}",
            received_at=BASE_TIME + timedelta(minutes=index),
        )

    snapshot = reads.get_conversation_observation_snapshot(
        tenant_id=TENANT_A,
        now=BASE_TIME + timedelta(minutes=ATTENTION_TURN_THRESHOLD),
        idle_threshold=timedelta(hours=4),
    )

    item = snapshot.items[0]
    assert item.turn_count == ATTENTION_TURN_THRESHOLD
    assert item.is_idle is False
    assert item.needs_operator_attention is True


def test_needs_operator_attention_true_via_idle_branch(tmp_path: Path) -> None:
    store, reads = _stores(tmp_path)
    session = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )
    store.append_turn_if_new(
        tenant_id=TENANT_A,
        conversation_id=session.conversation_id,
        message_sid="SM_ATTENTION_IDLE",
        from_number=FROM_NUMBER,
        body="hola",
        received_at=BASE_TIME,
    )

    snapshot = reads.get_conversation_observation_snapshot(
        tenant_id=TENANT_A,
        now=BASE_TIME + timedelta(hours=5),
        idle_threshold=timedelta(hours=4),
    )

    item = snapshot.items[0]
    assert item.turn_count < ATTENTION_TURN_THRESHOLD
    assert item.is_idle is True
    assert item.needs_operator_attention is True


def test_needs_operator_attention_false_when_neither_condition_is_true(
    tmp_path: Path,
) -> None:
    store, reads = _stores(tmp_path)
    session = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )
    store.append_turn_if_new(
        tenant_id=TENANT_A,
        conversation_id=session.conversation_id,
        message_sid="SM_ATTENTION_FRESH",
        from_number=FROM_NUMBER,
        body="hola",
        received_at=BASE_TIME,
    )

    snapshot = reads.get_conversation_observation_snapshot(
        tenant_id=TENANT_A,
        now=BASE_TIME + timedelta(minutes=10),
        idle_threshold=timedelta(hours=4),
    )

    item = snapshot.items[0]
    assert item.turn_count < ATTENTION_TURN_THRESHOLD
    assert item.is_idle is False
    assert item.needs_operator_attention is False


def test_needs_operator_attention_false_after_draft_created(tmp_path: Path) -> None:
    store, reads = _stores(tmp_path)
    session = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )

    for index in range(ATTENTION_TURN_THRESHOLD):
        store.append_turn_if_new(
            tenant_id=TENANT_A,
            conversation_id=session.conversation_id,
            message_sid=f"SM_ATTENTION_DRAFT_{index}",
            from_number=FROM_NUMBER,
            body=f"mensaje {index}",
            received_at=BASE_TIME + timedelta(minutes=index),
        )

    store.mark_draft_created(
        tenant_id=TENANT_A,
        conversation_id=session.conversation_id,
        order_id="ord_observation_attention",
    )

    snapshot = reads.get_conversation_observation_snapshot(
        tenant_id=TENANT_A,
        now=BASE_TIME + timedelta(hours=5),
        idle_threshold=timedelta(hours=4),
    )

    item = snapshot.items[0]
    assert item.status == "draft_created"
    assert item.turn_count >= ATTENTION_TURN_THRESHOLD
    assert item.is_idle is True
    assert item.needs_operator_attention is False


def test_diagnostics_counts_match_assembled_items(tmp_path: Path) -> None:
    store, reads = _stores(tmp_path)
    now = BASE_TIME + timedelta(hours=5)
    idle_threshold = timedelta(hours=4)

    fresh_open = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone="whatsapp:+573000000010",
        received_at=now - timedelta(hours=1),
    )
    store.append_turn_if_new(
        tenant_id=TENANT_A,
        conversation_id=fresh_open.conversation_id,
        message_sid="SM_DIAG_FRESH",
        from_number=FROM_NUMBER,
        body="hola",
        received_at=now - timedelta(hours=1),
    )

    idle_open = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone="whatsapp:+573000000011",
        received_at=BASE_TIME,
    )
    store.append_turn_if_new(
        tenant_id=TENANT_A,
        conversation_id=idle_open.conversation_id,
        message_sid="SM_DIAG_IDLE",
        from_number=FROM_NUMBER,
        body="hola",
        received_at=BASE_TIME,
    )

    busy_open = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone="whatsapp:+573000000012",
        received_at=now - timedelta(hours=1),
    )
    for index in range(ATTENTION_TURN_THRESHOLD):
        store.append_turn_if_new(
            tenant_id=TENANT_A,
            conversation_id=busy_open.conversation_id,
            message_sid=f"SM_DIAG_BUSY_{index}",
            from_number=FROM_NUMBER,
            body=f"mensaje {index}",
            received_at=now - timedelta(hours=1) + timedelta(minutes=index),
        )

    drafted_idle = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone="whatsapp:+573000000013",
        received_at=BASE_TIME,
    )
    store.append_turn_if_new(
        tenant_id=TENANT_A,
        conversation_id=drafted_idle.conversation_id,
        message_sid="SM_DIAG_DRAFT",
        from_number=FROM_NUMBER,
        body="hola",
        received_at=BASE_TIME,
    )
    store.mark_draft_created(
        tenant_id=TENANT_A,
        conversation_id=drafted_idle.conversation_id,
        order_id="ord_observation_diagnostics",
    )

    snapshot = reads.get_conversation_observation_snapshot(
        tenant_id=TENANT_A,
        now=now,
        idle_threshold=idle_threshold,
    )

    items = snapshot.items
    diagnostics = snapshot.diagnostics

    assert diagnostics.total_count == len(items) == 4
    assert diagnostics.open_count == sum(1 for item in items if item.status == "open") == 3
    assert (
        diagnostics.draft_created_count
        == sum(1 for item in items if item.status == "draft_created")
        == 1
    )
    assert diagnostics.idle_count == sum(1 for item in items if item.is_idle) == 2
    assert (
        diagnostics.needs_attention_count
        == sum(1 for item in items if item.needs_operator_attention)
        == 2
    )


def test_conversation_observation_reads_stays_outside_storage_interface() -> None:
    storage_methods = set(StorageInterface.__abstractmethods__)
    source = Path("src/duna_orders/storage/conversation_observation.py").read_text()

    assert "get_conversation_observation_snapshot" not in storage_methods
    assert "StorageInterface" not in source


# Detail read (M9.5B)


def test_detail_returns_none_for_unknown_conversation_id(tmp_path: Path) -> None:
    _, reads = _stores(tmp_path)

    detail = reads.get_conversation_observation_detail(
        tenant_id=TENANT_A,
        conversation_id="conv_does_not_exist",
        now=BASE_TIME,
    )

    assert detail is None


def test_detail_cross_tenant_lookup_returns_none(tmp_path: Path) -> None:
    store, reads = _stores(tmp_path)

    session_a = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )
    store.append_turn_if_new(
        tenant_id=TENANT_A,
        conversation_id=session_a.conversation_id,
        message_sid="SM_CROSS_TENANT",
        from_number=FROM_NUMBER,
        body="secreto del tenant A",
        received_at=BASE_TIME,
    )

    detail = reads.get_conversation_observation_detail(
        tenant_id=TENANT_B,
        conversation_id=session_a.conversation_id,
        now=BASE_TIME,
    )

    assert detail is None


def test_detail_returns_session_metadata_and_ordered_turns(tmp_path: Path) -> None:
    store, reads = _stores(tmp_path)
    session = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )

    for index in range(3):
        store.append_turn_if_new(
            tenant_id=TENANT_A,
            conversation_id=session.conversation_id,
            message_sid=f"SM_DETAIL_ORDER_{index}",
            from_number=FROM_NUMBER,
            body=f"mensaje {index}",
            received_at=BASE_TIME + timedelta(minutes=index),
        )

    detail = reads.get_conversation_observation_detail(
        tenant_id=TENANT_A,
        conversation_id=session.conversation_id,
        now=BASE_TIME,
    )

    assert detail is not None
    assert detail.session.conversation_id == session.conversation_id
    assert detail.session.tenant_id == TENANT_A
    assert detail.session.turn_count == 3
    assert [turn.message_sid for turn in detail.turns] == [
        "SM_DETAIL_ORDER_0",
        "SM_DETAIL_ORDER_1",
        "SM_DETAIL_ORDER_2",
    ]
    assert [turn.sequence_number for turn in detail.turns] == sorted(
        turn.sequence_number for turn in detail.turns
    )


def test_detail_zero_turn_session_returns_empty_turns_list(tmp_path: Path) -> None:
    store, reads = _stores(tmp_path)
    session = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )

    detail = reads.get_conversation_observation_detail(
        tenant_id=TENANT_A,
        conversation_id=session.conversation_id,
        now=BASE_TIME,
    )

    assert detail is not None
    assert detail.turns == []
    assert detail.session.turn_count == 0
    assert detail.session.latest_message_sid is None
    assert detail.session.latest_body_preview is None


def test_detail_single_turn_session_works(tmp_path: Path) -> None:
    store, reads = _stores(tmp_path)
    session = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )
    store.append_turn_if_new(
        tenant_id=TENANT_A,
        conversation_id=session.conversation_id,
        message_sid="SM_DETAIL_SINGLE",
        from_number=FROM_NUMBER,
        body="hola",
        received_at=BASE_TIME,
    )

    detail = reads.get_conversation_observation_detail(
        tenant_id=TENANT_A,
        conversation_id=session.conversation_id,
        now=BASE_TIME,
    )

    assert detail is not None
    assert len(detail.turns) == 1

    turn = detail.turns[0]
    assert turn.message_sid == "SM_DETAIL_SINGLE"
    assert turn.from_number == FROM_NUMBER
    assert turn.body_preview == "hola"
    assert turn.received_at == BASE_TIME
    assert detail.session.turn_count == 1
    assert detail.session.latest_message_sid == "SM_DETAIL_SINGLE"


def test_detail_turn_body_preview_truncates_long_body(tmp_path: Path) -> None:
    store, reads = _stores(tmp_path)
    session = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )
    long_body = "x" * (LATEST_BODY_PREVIEW_LENGTH + 50)
    store.append_turn_if_new(
        tenant_id=TENANT_A,
        conversation_id=session.conversation_id,
        message_sid="SM_DETAIL_LONG_BODY",
        from_number=FROM_NUMBER,
        body=long_body,
        received_at=BASE_TIME,
    )

    detail = reads.get_conversation_observation_detail(
        tenant_id=TENANT_A,
        conversation_id=session.conversation_id,
        now=BASE_TIME,
    )

    assert detail is not None
    preview = detail.turns[0].body_preview
    assert preview == long_body[:LATEST_BODY_PREVIEW_LENGTH]
    assert len(preview) == LATEST_BODY_PREVIEW_LENGTH


def test_detail_session_metadata_handles_null_advancement_and_parse_fields(
    tmp_path: Path,
) -> None:
    store, reads = _stores(tmp_path)
    session = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )

    detail = reads.get_conversation_observation_detail(
        tenant_id=TENANT_A,
        conversation_id=session.conversation_id,
        now=BASE_TIME,
    )

    assert detail is not None
    assert detail.session.latest_advancement_outcome is None
    assert detail.session.latest_parse_error_category is None
    assert detail.session.linked_order_id is None
    assert detail.session.has_draft is False


def test_detail_is_idle_reflects_now_and_idle_threshold(tmp_path: Path) -> None:
    store, reads = _stores(tmp_path)
    session = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )

    fresh_detail = reads.get_conversation_observation_detail(
        tenant_id=TENANT_A,
        conversation_id=session.conversation_id,
        now=BASE_TIME + timedelta(hours=2),
        idle_threshold=timedelta(hours=4),
    )
    idle_detail = reads.get_conversation_observation_detail(
        tenant_id=TENANT_A,
        conversation_id=session.conversation_id,
        now=BASE_TIME + timedelta(hours=5),
        idle_threshold=timedelta(hours=4),
    )

    assert fresh_detail is not None
    assert idle_detail is not None
    assert fresh_detail.session.status == "open"
    assert fresh_detail.session.is_idle is False
    assert idle_detail.session.status == "open"
    assert idle_detail.session.is_idle is True


def test_turn_observation_item_supports_missing_message_sid() -> None:
    item = ConversationTurnObservationItem(
        turn_id="turn_missing_sid",
        sequence_number=1,
        received_at=BASE_TIME,
        from_number=None,
        message_sid=None,
        body_preview="hola",
    )

    assert item.message_sid is None
    assert item.from_number is None
