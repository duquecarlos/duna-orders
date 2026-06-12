from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from duna_orders.storage.conversation_observation import (
    ConversationObservationItem,
    ConversationTurnObservationItem,
    PostgresConversationObservationReads,
)
from duna_orders.storage.conversation_state import PostgresConversationStateStore
from duna_orders.storage.postgres_base import Base
from duna_orders.storage.postgres_session import make_engine, make_session_factory
from duna_orders.ui.conversations import (
    ANY_VALUE_LABEL,
    NONE_VALUE_LABEL,
    OPEN_IDLE_LABEL,
    STATUS_FILTER_ALL,
    STATUS_LABELS,
    advancement_outcome_filter_options,
    conversation_detail_metadata_row,
    conversation_option_label,
    conversation_row,
    conversation_status_label,
    matches_filters,
    parse_error_category_filter_options,
    status_filter_options,
    turn_preview_row,
    turn_preview_rows,
)


TENANT_A = "tenant_conversations_ui_a"
TENANT_B = "tenant_conversations_ui_b"
BASE_TIME = datetime(2026, 6, 10, 14, 0, tzinfo=timezone.utc)


def _item(
    *,
    conversation_id: str = "conv-1",
    tenant_id: str = TENANT_A,
    customer_phone: str = "whatsapp:+573000000001",
    status: str = "open",
    last_message_at: datetime = BASE_TIME,
    is_idle: bool = False,
    latest_advancement_outcome: str | None = None,
    latest_parse_error_category: str | None = None,
    linked_order_id: str | None = None,
    latest_message_sid: str | None = None,
    latest_body_preview: str | None = None,
    has_draft: bool = False,
    needs_operator_attention: bool = False,
    version: int = 1,
    turn_count: int = 0,
) -> ConversationObservationItem:
    return ConversationObservationItem(
        conversation_id=conversation_id,
        tenant_id=tenant_id,
        customer_phone=customer_phone,
        status=status,  # type: ignore[arg-type]
        opened_at=BASE_TIME,
        last_message_at=last_message_at,
        updated_at=BASE_TIME,
        version=version,
        turn_count=turn_count,
        latest_message_sid=latest_message_sid,
        latest_body_preview=latest_body_preview,
        linked_order_id=linked_order_id,
        has_draft=has_draft,
        is_idle=is_idle,
        needs_operator_attention=needs_operator_attention,
        latest_advancement_outcome=latest_advancement_outcome,
        latest_parse_error_category=latest_parse_error_category,
    )


def _turn_item(
    *,
    turn_id: str = "turn-1",
    sequence_number: int = 1,
    received_at: datetime = BASE_TIME,
    from_number: str | None = "whatsapp:+573000000001",
    message_sid: str | None = "SM_TEST",
    body_preview: str | None = "hola",
) -> ConversationTurnObservationItem:
    return ConversationTurnObservationItem(
        turn_id=turn_id,
        sequence_number=sequence_number,
        received_at=received_at,
        from_number=from_number,
        message_sid=message_sid,
        body_preview=body_preview,
    )


def _no_filters(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "status": STATUS_FILTER_ALL,
        "customer_phone_query": "",
        "latest_advancement_outcome": ANY_VALUE_LABEL,
        "latest_parse_error_category": ANY_VALUE_LABEL,
        "recent_activity_since": None,
    }
    defaults.update(overrides)
    return defaults


# Idle presentation (requirement 5)


def test_conversation_status_label_marks_open_idle_as_distinct_from_open() -> None:
    open_item = _item(status="open", is_idle=False)
    open_idle_item = _item(status="open", is_idle=True)

    open_label = conversation_status_label(open_item)
    open_idle_label = conversation_status_label(open_idle_item)

    assert open_label == STATUS_LABELS["open"]
    assert open_idle_label == OPEN_IDLE_LABEL
    assert open_idle_label != open_label


def test_conversation_status_label_for_draft_created_is_unaffected_by_idle() -> None:
    item = _item(status="draft_created", is_idle=True)

    assert conversation_status_label(item) == STATUS_LABELS["draft_created"]


def test_conversation_row_marks_open_idle_distinctly() -> None:
    row = conversation_row(_item(status="open", is_idle=True))

    assert row["Status"] == OPEN_IDLE_LABEL
    assert row["Status"] != STATUS_LABELS["open"]


# Graceful rendering (requirement 4)


def test_conversation_row_handles_null_fields_gracefully() -> None:
    item = _item(
        latest_advancement_outcome=None,
        latest_parse_error_category=None,
        linked_order_id=None,
        latest_message_sid=None,
        latest_body_preview=None,
    )

    row = conversation_row(item)

    assert row["Latest advancement outcome"] == "Not set"
    assert row["Latest parse error category"] == "Not set"
    assert row["Linked order ID"] == "Not set"
    assert row["Latest message SID"] == "Not set"
    assert row["Latest message preview"] == "Not set"


def test_status_filter_options_handles_empty_list() -> None:
    assert status_filter_options([]) == [STATUS_FILTER_ALL]


def test_advancement_outcome_filter_options_handles_empty_list() -> None:
    assert advancement_outcome_filter_options([]) == [ANY_VALUE_LABEL]


def test_parse_error_category_filter_options_handles_empty_list() -> None:
    assert parse_error_category_filter_options([]) == [ANY_VALUE_LABEL]


def test_advancement_outcome_filter_options_includes_none_marker_when_present() -> None:
    items = [
        _item(conversation_id="c1", latest_advancement_outcome="DRAFT_CREATED"),
        _item(conversation_id="c2", latest_advancement_outcome=None),
    ]

    options = advancement_outcome_filter_options(items)

    assert options == [ANY_VALUE_LABEL, NONE_VALUE_LABEL, "DRAFT_CREATED"]


def test_empty_item_list_filters_to_empty_list() -> None:
    items: list[ConversationObservationItem] = []

    filtered = [item for item in items if matches_filters(item, **_no_filters())]

    assert filtered == []


# Filter behavior


def test_matches_filters_status_filter() -> None:
    open_item = _item(conversation_id="c1", status="open")
    draft_item = _item(conversation_id="c2", status="draft_created")

    assert matches_filters(open_item, **_no_filters(status="open"))
    assert not matches_filters(draft_item, **_no_filters(status="open"))


def test_matches_filters_customer_phone_search_is_case_insensitive_substring() -> None:
    item = _item(customer_phone="whatsapp:+573001112233")

    assert matches_filters(item, **_no_filters(customer_phone_query="3001112233"))
    assert matches_filters(item, **_no_filters(customer_phone_query="WHATSAPP"))
    assert not matches_filters(item, **_no_filters(customer_phone_query="9999999999"))


def test_matches_filters_none_marker_for_advancement_outcome() -> None:
    no_outcome = _item(latest_advancement_outcome=None)
    with_outcome = _item(latest_advancement_outcome="DRAFT_CREATED")

    assert matches_filters(
        no_outcome, **_no_filters(latest_advancement_outcome=NONE_VALUE_LABEL)
    )
    assert not matches_filters(
        with_outcome, **_no_filters(latest_advancement_outcome=NONE_VALUE_LABEL)
    )
    assert matches_filters(
        with_outcome, **_no_filters(latest_advancement_outcome="DRAFT_CREATED")
    )


def test_matches_filters_none_marker_for_parse_error_category() -> None:
    no_category = _item(latest_parse_error_category=None)
    with_category = _item(latest_parse_error_category="PARSER_ERROR")

    assert matches_filters(
        no_category, **_no_filters(latest_parse_error_category=NONE_VALUE_LABEL)
    )
    assert not matches_filters(
        with_category, **_no_filters(latest_parse_error_category=NONE_VALUE_LABEL)
    )
    assert matches_filters(
        with_category, **_no_filters(latest_parse_error_category="PARSER_ERROR")
    )


def test_matches_filters_recent_activity_window() -> None:
    item = _item(last_message_at=BASE_TIME)

    assert matches_filters(
        item, **_no_filters(recent_activity_since=BASE_TIME - timedelta(hours=1))
    )
    assert not matches_filters(
        item, **_no_filters(recent_activity_since=BASE_TIME + timedelta(hours=1))
    )


# Tenant scoping (requirement 1)


def _stores(
    tmp_path: Path,
) -> tuple[PostgresConversationStateStore, PostgresConversationObservationReads]:
    database_path = tmp_path / "conversations_ui.db"
    engine = make_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    return (
        PostgresConversationStateStore(session_factory),
        PostgresConversationObservationReads(session_factory),
    )


def test_conversation_list_read_is_tenant_scoped_for_operator_page(tmp_path: Path) -> None:
    store, reads = _stores(tmp_path)

    session_a = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone="whatsapp:+573001110000",
        received_at=BASE_TIME,
    )
    session_b = store.get_or_create_open_session(
        tenant_id=TENANT_B,
        customer_phone="whatsapp:+573002220000",
        received_at=BASE_TIME,
    )

    snapshot_a = reads.get_conversation_observation_snapshot(tenant_id=TENANT_A, now=BASE_TIME)
    rows_a = [conversation_row(item) for item in snapshot_a.items]

    assert [item.conversation_id for item in snapshot_a.items] == [session_a.conversation_id]
    assert all(item.tenant_id == TENANT_A for item in snapshot_a.items)
    assert not any(row["Conversation ID"] == session_b.conversation_id for row in rows_a)
    assert not any(row["Customer phone"] == "whatsapp:+573002220000" for row in rows_a)


# Session detail metadata (M9.5B)


def test_conversation_detail_metadata_row_handles_null_fields_gracefully() -> None:
    item = _item(
        latest_advancement_outcome=None,
        latest_parse_error_category=None,
        linked_order_id=None,
    )

    row = conversation_detail_metadata_row(item)

    assert row["Latest advancement outcome"] == "Not set"
    assert row["Latest parse error category"] == "Not set"
    assert row["Linked order ID"] == "Not set"


def test_conversation_detail_metadata_row_marks_open_idle_distinctly() -> None:
    row = conversation_detail_metadata_row(_item(status="open", is_idle=True))

    assert row["Status"] == OPEN_IDLE_LABEL
    assert row["Status"] != STATUS_LABELS["open"]


def test_conversation_detail_metadata_row_fresh_open_renders_plain_open() -> None:
    row = conversation_detail_metadata_row(_item(status="open", is_idle=False))

    assert row["Status"] == STATUS_LABELS["open"]


def test_conversation_detail_metadata_row_includes_required_fields() -> None:
    item = _item(
        conversation_id="conv-detail",
        turn_count=5,
        version=3,
        needs_operator_attention=True,
        has_draft=True,
        linked_order_id="ord-1",
    )

    row = conversation_detail_metadata_row(item)

    assert row["Conversation ID"] == "conv-detail"
    assert row["Customer phone"] == item.customer_phone
    assert row["Last message at"] == item.last_message_at.isoformat()
    assert row["Version"] == 3
    assert row["Turns"] == 5
    assert row["Linked order ID"] == "ord-1"
    assert row["Has draft"] is True
    assert row["Observed idle"] == item.is_idle
    assert row["Needs operator attention"] is True


def test_conversation_option_label_includes_phone_status_and_conversation_id() -> None:
    item = _item(
        conversation_id="conv-option",
        customer_phone="whatsapp:+573009990000",
        status="open",
        is_idle=True,
    )

    label = conversation_option_label(item)

    assert "whatsapp:+573009990000" in label
    assert OPEN_IDLE_LABEL in label
    assert "conv-option" in label


# Turn preview rendering (M9.5B)


def test_turn_preview_row_handles_missing_message_sid_and_from_number_gracefully() -> None:
    turn = _turn_item(message_sid=None, from_number=None, body_preview=None)

    row = turn_preview_row(turn)

    assert row["Message SID"] == "Not set"
    assert row["From number"] == "Not set"
    assert row["Body preview"] == "Not set"


def test_turn_preview_rows_renders_in_order() -> None:
    turns = [
        _turn_item(turn_id="turn-1", sequence_number=1, message_sid="SM_1"),
        _turn_item(turn_id="turn-2", sequence_number=2, message_sid="SM_2"),
        _turn_item(turn_id="turn-3", sequence_number=3, message_sid="SM_3"),
    ]

    rows = turn_preview_rows(turns)

    assert [row["Message SID"] for row in rows] == ["SM_1", "SM_2", "SM_3"]
    assert [row["Sequence"] for row in rows] == [1, 2, 3]


def test_turn_preview_rows_handles_zero_turns() -> None:
    assert turn_preview_rows([]) == []
