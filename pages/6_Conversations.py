from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from duna_orders.demo_catalog import DemoCatalogFile
from duna_orders.storage.base import StorageInterface
from duna_orders.storage.conversation_observation import PostgresConversationObservationReads
from duna_orders.storage.read_context import sheets_request_context
from duna_orders.ui.conversations import (
    OPEN_IDLE_HELP_MESSAGE,
    POSTGRES_ONLY_MESSAGE,
    RECENT_ACTIVITY_OPTIONS,
    advancement_outcome_filter_options,
    conversation_row,
    matches_filters,
    operator_list_load_error_message,
    parse_error_category_filter_options,
    status_filter_options,
)
from duna_orders.ui.setup import (
    get_conversation_observation_reads,
    get_demo_catalog,
    get_storage,
)


def _bootstrap_session() -> None:
    if "demo_catalog" not in st.session_state:
        st.session_state.demo_catalog = get_demo_catalog()

    if "storage" not in st.session_state:
        st.session_state.storage = get_storage()

    if "conversation_observation_reads" not in st.session_state:
        st.session_state.conversation_observation_reads = (
            get_conversation_observation_reads(st.session_state.storage)
        )


st.set_page_config(page_title="Conversations", page_icon="C", layout="wide")

st.title("Conversations")
st.caption("Read-only view of recent customer conversation sessions.")

_bootstrap_session()

catalog: DemoCatalogFile = st.session_state.demo_catalog
storage: StorageInterface = st.session_state.storage
reads: PostgresConversationObservationReads | None = (
    st.session_state.conversation_observation_reads
)
tenant_id = catalog.business.tenant_id

with sheets_request_context(storage):
    with st.sidebar:
        st.subheader("Demo")
        st.write(f"Backend: `{storage.__class__.__name__}`")
        st.write(f"Business: `{catalog.business.business_name}`")
        st.write(f"Tenant: `{tenant_id}`")

        if st.button("Refresh"):
            st.rerun()

    if reads is None:
        st.info(POSTGRES_ONLY_MESSAGE)
        st.stop()

    now = datetime.now(timezone.utc)

    try:
        snapshot = reads.get_conversation_observation_snapshot(
            tenant_id=tenant_id,
            now=now,
        )
    except Exception as error:
        st.error(operator_list_load_error_message(error))
        st.stop()

    items = snapshot.items

    st.info(OPEN_IDLE_HELP_MESSAGE)

    status_col, phone_col = st.columns(2)
    with status_col:
        status_filter = st.selectbox("Status", status_filter_options(items))
    with phone_col:
        phone_query = st.text_input("Customer phone contains")

    outcome_col, category_col, activity_col = st.columns(3)
    with outcome_col:
        outcome_filter = st.selectbox(
            "Latest advancement outcome",
            advancement_outcome_filter_options(items),
        )
    with category_col:
        category_filter = st.selectbox(
            "Latest parse error category",
            parse_error_category_filter_options(items),
        )
    with activity_col:
        activity_label = st.selectbox(
            "Recent activity",
            list(RECENT_ACTIVITY_OPTIONS),
        )

    activity_window = RECENT_ACTIVITY_OPTIONS[activity_label]
    recent_activity_since = now - activity_window if activity_window is not None else None

    filtered_items = [
        item
        for item in items
        if matches_filters(
            item,
            status=status_filter,
            customer_phone_query=phone_query,
            latest_advancement_outcome=outcome_filter,
            latest_parse_error_category=category_filter,
            recent_activity_since=recent_activity_since,
        )
    ]

    st.caption(f"Showing {len(filtered_items)} of {len(items)} conversation sessions.")

    if not filtered_items:
        st.info("No conversation sessions match the current filters.")
    else:
        st.dataframe(
            [conversation_row(item) for item in filtered_items],
            use_container_width=True,
            hide_index=True,
        )
