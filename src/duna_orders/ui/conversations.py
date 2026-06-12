from __future__ import annotations

from datetime import datetime, timedelta

from duna_orders.storage.conversation_observation import (
    ConversationObservationItem,
    ConversationTurnObservationItem,
)


POSTGRES_ONLY_MESSAGE = (
    "Conversation visibility is available only with the Postgres backend."
)
LIST_LOAD_FAILURE_MESSAGE = (
    "Conversation sessions could not be loaded. Refresh the page; if the "
    "problem continues, escalate for manual review."
)

NOT_SET_LABEL = "Not set"

STATUS_FILTER_ALL = "All"
ANY_VALUE_LABEL = "All"
NONE_VALUE_LABEL = "(none)"

STATUS_LABELS: dict[str, str] = {
    "open": "Open",
    "draft_created": "Draft created",
    "expired": "Expired",
    "failed": "Failed",
}

OPEN_IDLE_LABEL = "Open - observed idle (not expired)"
OPEN_IDLE_HELP_MESSAGE = (
    "\"Open - observed idle\" means no message has arrived within the idle "
    "threshold when this page was loaded. This is a read-time observation, "
    "not a persisted session state: the session remains status=\"open\" in "
    "storage and the runtime does not mark sessions as expired."
)

SESSION_DETAIL_SELECT_LABEL = "Select a session for details"
SESSION_NOT_FOUND_MESSAGE = (
    "Session not found for this tenant. It may have been removed since the "
    "list was loaded."
)
NO_TURNS_MESSAGE = "No turns recorded for this session yet."

RECENT_ACTIVITY_ANY_LABEL = "Any time"
RECENT_ACTIVITY_OPTIONS: dict[str, timedelta | None] = {
    RECENT_ACTIVITY_ANY_LABEL: None,
    "Last hour": timedelta(hours=1),
    "Last 4 hours": timedelta(hours=4),
    "Last 24 hours": timedelta(hours=24),
    "Last 7 days": timedelta(days=7),
}


def operator_list_load_error_message(error: Exception) -> str:
    return LIST_LOAD_FAILURE_MESSAGE


def display_value(value: object) -> str:
    if value is None or value == "":
        return NOT_SET_LABEL

    return str(value)


def conversation_status_label(item: ConversationObservationItem) -> str:
    if item.status == "open" and item.is_idle:
        return OPEN_IDLE_LABEL

    return STATUS_LABELS.get(item.status, item.status)


def status_filter_options(items: list[ConversationObservationItem]) -> list[str]:
    statuses = sorted({item.status for item in items})
    return [STATUS_FILTER_ALL, *statuses]


def advancement_outcome_filter_options(
    items: list[ConversationObservationItem],
) -> list[str]:
    return _value_filter_options(item.latest_advancement_outcome for item in items)


def parse_error_category_filter_options(
    items: list[ConversationObservationItem],
) -> list[str]:
    return _value_filter_options(item.latest_parse_error_category for item in items)


def _value_filter_options(values: object) -> list[str]:
    seen_none = False
    distinct_values: set[str] = set()

    for value in values:
        if value is None:
            seen_none = True
        else:
            distinct_values.add(value)

    options = [ANY_VALUE_LABEL]
    if seen_none:
        options.append(NONE_VALUE_LABEL)

    options.extend(sorted(distinct_values))
    return options


def matches_filters(
    item: ConversationObservationItem,
    *,
    status: str,
    customer_phone_query: str,
    latest_advancement_outcome: str,
    latest_parse_error_category: str,
    recent_activity_since: datetime | None,
) -> bool:
    if status != STATUS_FILTER_ALL and item.status != status:
        return False

    query = customer_phone_query.strip().lower()
    if query and query not in item.customer_phone.lower():
        return False

    if not _matches_optional_value(
        latest_advancement_outcome, item.latest_advancement_outcome
    ):
        return False

    if not _matches_optional_value(
        latest_parse_error_category, item.latest_parse_error_category
    ):
        return False

    if recent_activity_since is not None and item.last_message_at < recent_activity_since:
        return False

    return True


def _matches_optional_value(filter_value: str, item_value: str | None) -> bool:
    if filter_value == ANY_VALUE_LABEL:
        return True

    if filter_value == NONE_VALUE_LABEL:
        return item_value is None

    return item_value == filter_value


def conversation_row(item: ConversationObservationItem) -> dict[str, object]:
    return {
        "Conversation ID": item.conversation_id,
        "Customer phone": item.customer_phone,
        "Status": conversation_status_label(item),
        "Last message at": item.last_message_at.isoformat(),
        "Version": item.version,
        "Turns": item.turn_count,
        "Latest message SID": display_value(item.latest_message_sid),
        "Latest message preview": display_value(item.latest_body_preview),
        "Linked order ID": display_value(item.linked_order_id),
        "Has draft": item.has_draft,
        "Observed idle": item.is_idle,
        "Latest advancement outcome": display_value(item.latest_advancement_outcome),
        "Latest parse error category": display_value(item.latest_parse_error_category),
        "Needs operator attention": item.needs_operator_attention,
    }


def conversation_option_label(item: ConversationObservationItem) -> str:
    return f"{item.customer_phone} | {conversation_status_label(item)} | {item.conversation_id}"


def conversation_detail_metadata_row(item: ConversationObservationItem) -> dict[str, object]:
    return {
        "Conversation ID": item.conversation_id,
        "Customer phone": item.customer_phone,
        "Status": conversation_status_label(item),
        "Last message at": item.last_message_at.isoformat(),
        "Version": item.version,
        "Turns": item.turn_count,
        "Linked order ID": display_value(item.linked_order_id),
        "Has draft": item.has_draft,
        "Observed idle": item.is_idle,
        "Latest advancement outcome": display_value(item.latest_advancement_outcome),
        "Latest parse error category": display_value(item.latest_parse_error_category),
        "Needs operator attention": item.needs_operator_attention,
    }


def turn_preview_row(turn: ConversationTurnObservationItem) -> dict[str, object]:
    return {
        "Sequence": turn.sequence_number,
        "Received at": turn.received_at.isoformat(),
        "From number": display_value(turn.from_number),
        "Message SID": display_value(turn.message_sid),
        "Body preview": display_value(turn.body_preview),
    }


def turn_preview_rows(
    turns: list[ConversationTurnObservationItem],
) -> list[dict[str, object]]:
    return [turn_preview_row(turn) for turn in turns]
