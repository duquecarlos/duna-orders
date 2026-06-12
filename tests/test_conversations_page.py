from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from streamlit.testing.v1 import AppTest

from duna_orders.demo_catalog import load_demo_catalog
from duna_orders.storage.conversation_observation import (
    PostgresConversationObservationReads,
)
from duna_orders.storage.conversation_state import PostgresConversationStateStore
from duna_orders.storage.postgres import PostgresStorage
from duna_orders.storage.postgres_base import Base
from duna_orders.storage.postgres_session import make_engine, make_session_factory
from duna_orders.ui.conversations import (
    NO_TURNS_MESSAGE,
    OPEN_IDLE_LABEL,
    SESSION_NOT_FOUND_MESSAGE,
    STATUS_LABELS,
)


TENANT_ID = "el-fogon-colombiano"


def _session_factory(tmp_path: Path, name: str):
    database_path = tmp_path / name
    engine = make_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def _conversations_app(*, session_factory) -> AppTest:
    app = AppTest.from_file("pages/6_Conversations.py", default_timeout=10)
    app.session_state["demo_catalog"] = load_demo_catalog()
    app.session_state["storage"] = PostgresStorage(session_factory)
    app.session_state["conversation_observation_reads"] = (
        PostgresConversationObservationReads(session_factory)
    )
    return app


def test_conversations_page_renders_empty_list_without_error(tmp_path: Path) -> None:
    session_factory = _session_factory(tmp_path, "conversations_page_empty.db")
    app = _conversations_app(session_factory=session_factory)

    app.run()

    assert app.exception == []
    assert app.dataframe == []
    assert any(
        "No conversation sessions match" in info.value for info in app.info
    )


def test_conversations_page_renders_open_idle_session_with_distinct_label(
    tmp_path: Path,
) -> None:
    session_factory = _session_factory(tmp_path, "conversations_page_idle.db")
    store = PostgresConversationStateStore(session_factory)
    now = datetime.now(timezone.utc)
    store.get_or_create_open_session(
        tenant_id=TENANT_ID,
        customer_phone="whatsapp:+573009998888",
        received_at=now - timedelta(hours=5),
    )

    app = _conversations_app(session_factory=session_factory)
    app.run()

    assert app.exception == []
    dataframe = app.dataframe[0].value
    assert len(dataframe) == 1
    row = dataframe.iloc[0]
    assert row["Status"] == OPEN_IDLE_LABEL
    assert row["Status"] != STATUS_LABELS["open"]
    assert row["Linked order ID"] == "Not set"
    assert row["Latest message SID"] == "Not set"
    assert row["Latest advancement outcome"] == "Not set"
    assert row["Latest parse error category"] == "Not set"

    detail_row = app.dataframe[1].value.iloc[0]
    assert detail_row["Status"] == OPEN_IDLE_LABEL
    assert detail_row["Status"] != STATUS_LABELS["open"]
    assert any(NO_TURNS_MESSAGE in info.value for info in app.info)


def test_conversations_page_renders_fresh_open_session_as_plain_open(
    tmp_path: Path,
) -> None:
    session_factory = _session_factory(tmp_path, "conversations_page_fresh.db")
    store = PostgresConversationStateStore(session_factory)
    now = datetime.now(timezone.utc)
    store.get_or_create_open_session(
        tenant_id=TENANT_ID,
        customer_phone="whatsapp:+573001112222",
        received_at=now,
    )

    app = _conversations_app(session_factory=session_factory)
    app.run()

    assert app.exception == []
    dataframe = app.dataframe[0].value
    assert len(dataframe) == 1
    assert dataframe.iloc[0]["Status"] == STATUS_LABELS["open"]

    detail_row = app.dataframe[1].value.iloc[0]
    assert detail_row["Status"] == STATUS_LABELS["open"]


def test_conversations_page_renders_zero_turn_detail_gracefully(tmp_path: Path) -> None:
    session_factory = _session_factory(tmp_path, "conversations_page_zero_turns.db")
    store = PostgresConversationStateStore(session_factory)
    now = datetime.now(timezone.utc)
    store.get_or_create_open_session(
        tenant_id=TENANT_ID,
        customer_phone="whatsapp:+573008889999",
        received_at=now,
    )

    app = _conversations_app(session_factory=session_factory)
    app.run()

    assert app.exception == []
    assert len(app.dataframe) == 2
    assert any(NO_TURNS_MESSAGE in info.value for info in app.info)


def test_conversations_page_detail_handles_null_fields_gracefully(tmp_path: Path) -> None:
    session_factory = _session_factory(tmp_path, "conversations_page_detail_nulls.db")
    store = PostgresConversationStateStore(session_factory)
    now = datetime.now(timezone.utc)
    store.get_or_create_open_session(
        tenant_id=TENANT_ID,
        customer_phone="whatsapp:+573007778888",
        received_at=now,
    )

    app = _conversations_app(session_factory=session_factory)
    app.run()

    assert app.exception == []
    detail_row = app.dataframe[1].value.iloc[0]
    assert detail_row["Linked order ID"] == "Not set"
    assert detail_row["Latest advancement outcome"] == "Not set"
    assert detail_row["Latest parse error category"] == "Not set"


def test_conversations_page_renders_ordered_turn_previews(tmp_path: Path) -> None:
    session_factory = _session_factory(tmp_path, "conversations_page_turns.db")
    store = PostgresConversationStateStore(session_factory)
    now = datetime.now(timezone.utc)
    session = store.get_or_create_open_session(
        tenant_id=TENANT_ID,
        customer_phone="whatsapp:+573006667777",
        received_at=now,
    )
    for index in range(3):
        store.append_turn_if_new(
            tenant_id=TENANT_ID,
            conversation_id=session.conversation_id,
            message_sid=f"SM_PAGE_TURN_{index}",
            from_number="whatsapp:+573006667777",
            body=f"mensaje {index}",
            received_at=now + timedelta(minutes=index),
        )

    app = _conversations_app(session_factory=session_factory)
    app.run()

    assert app.exception == []
    assert len(app.dataframe) == 3

    turns_dataframe = app.dataframe[2].value
    assert list(turns_dataframe["Message SID"]) == [
        "SM_PAGE_TURN_0",
        "SM_PAGE_TURN_1",
        "SM_PAGE_TURN_2",
    ]
    assert list(turns_dataframe["Sequence"]) == sorted(turns_dataframe["Sequence"])


class _DetailNoneReads:
    def __init__(self, reads: PostgresConversationObservationReads) -> None:
        self._reads = reads

    def get_conversation_observation_snapshot(self, **kwargs):
        return self._reads.get_conversation_observation_snapshot(**kwargs)

    def get_conversation_observation_detail(self, **kwargs):
        return None


def test_conversations_page_renders_not_found_message_when_detail_read_returns_none(
    tmp_path: Path,
) -> None:
    session_factory = _session_factory(tmp_path, "conversations_page_detail_none.db")
    store = PostgresConversationStateStore(session_factory)
    now = datetime.now(timezone.utc)
    store.get_or_create_open_session(
        tenant_id=TENANT_ID,
        customer_phone="whatsapp:+573004445555",
        received_at=now,
    )

    app = _conversations_app(session_factory=session_factory)
    app.session_state["conversation_observation_reads"] = _DetailNoneReads(
        PostgresConversationObservationReads(session_factory)
    )

    app.run()

    assert app.exception == []
    assert any(SESSION_NOT_FOUND_MESSAGE in warning.value for warning in app.warning)
