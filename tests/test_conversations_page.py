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
from duna_orders.ui.conversations import OPEN_IDLE_LABEL, STATUS_LABELS


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
