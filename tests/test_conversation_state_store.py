from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.command import upgrade
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from duna_orders.domain.models import Order, OrderItem
from duna_orders.config import settings
from duna_orders.storage.conversation_state import PostgresConversationStateStore
from duna_orders.storage.conversation_orders import PostgresConversationOrderLookup
from duna_orders.storage.postgres import PostgresStorage
from duna_orders.storage.postgres_base import Base
from duna_orders.storage.postgres_models import ConversationSessionRow
from duna_orders.storage.postgres_session import make_engine, make_session_factory


TENANT_A = "tenant_conv_a"
TENANT_B = "tenant_conv_b"
CUSTOMER_PHONE = "whatsapp:+573001112233"
FROM_NUMBER = "whatsapp:+573001112233"
BASE_TIME = datetime(2026, 6, 10, 14, 0, tzinfo=timezone.utc)


def _store(tmp_path: Path) -> PostgresConversationStateStore:
    database_path = tmp_path / "conversation_state.db"
    engine = make_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    return PostgresConversationStateStore(make_session_factory(engine))


def test_get_or_create_open_session_creates_open_session_with_version(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    session = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )

    assert session.tenant_id == TENANT_A
    assert session.customer_phone == CUSTOMER_PHONE
    assert session.status == "open"
    assert session.opened_at == BASE_TIME
    assert session.last_message_at == BASE_TIME
    assert session.version == 1


def test_get_or_create_open_session_reuses_existing_open_session(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    first = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )
    second = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME + timedelta(minutes=3),
    )

    assert second.conversation_id == first.conversation_id
    assert second.opened_at == BASE_TIME


def test_append_turn_if_new_appends_once_and_updates_session_last_message_at(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    session = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )
    turn_time = BASE_TIME + timedelta(minutes=2)

    result = store.append_turn_if_new(
        tenant_id=TENANT_A,
        conversation_id=session.conversation_id,
        message_sid="SM_CONV_APPEND",
        from_number=FROM_NUMBER,
        body="hola",
        received_at=turn_time,
    )

    updated = store.get_session(
        tenant_id=TENANT_A,
        conversation_id=session.conversation_id,
    )
    turns = store.list_turns(
        tenant_id=TENANT_A,
        conversation_id=session.conversation_id,
    )

    assert result.appended is True
    assert result.turn.sequence_number == 1
    assert updated is not None
    assert updated.last_message_at == turn_time
    assert updated.version == 2
    assert [turn.message_sid for turn in turns] == ["SM_CONV_APPEND"]


def test_append_turn_if_new_is_idempotent_for_same_tenant_message_sid(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    session = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )

    first = store.append_turn_if_new(
        tenant_id=TENANT_A,
        conversation_id=session.conversation_id,
        message_sid="SM_CONV_DUP",
        from_number=FROM_NUMBER,
        body="hola",
        received_at=BASE_TIME,
    )
    second = store.append_turn_if_new(
        tenant_id=TENANT_A,
        conversation_id=session.conversation_id,
        message_sid="SM_CONV_DUP",
        from_number=FROM_NUMBER,
        body="hola again",
        received_at=BASE_TIME + timedelta(minutes=1),
    )

    turns = store.list_turns(
        tenant_id=TENANT_A,
        conversation_id=session.conversation_id,
    )

    assert first.appended is True
    assert second.appended is False
    assert second.turn.turn_id == first.turn.turn_id
    assert len(turns) == 1
    assert turns[0].body == "hola"


def test_same_customer_and_message_sid_are_isolated_by_tenant(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session_a = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )
    session_b = store.get_or_create_open_session(
        tenant_id=TENANT_B,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )

    turn_a = store.append_turn_if_new(
        tenant_id=TENANT_A,
        conversation_id=session_a.conversation_id,
        message_sid="SM_SHARED_BY_TENANT",
        from_number=FROM_NUMBER,
        body="tenant a",
        received_at=BASE_TIME,
    )
    turn_b = store.append_turn_if_new(
        tenant_id=TENANT_B,
        conversation_id=session_b.conversation_id,
        message_sid="SM_SHARED_BY_TENANT",
        from_number=FROM_NUMBER,
        body="tenant b",
        received_at=BASE_TIME,
    )

    assert session_a.conversation_id != session_b.conversation_id
    assert turn_a.appended is True
    assert turn_b.appended is True
    assert turn_a.turn.turn_id != turn_b.turn.turn_id


def test_list_turns_returns_sequence_number_order(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )

    for index, body in enumerate(["hola", "tienen bandeja?", "2 porfa"], start=1):
        store.append_turn_if_new(
            tenant_id=TENANT_A,
            conversation_id=session.conversation_id,
            message_sid=f"SM_ORDERED_{index}",
            from_number=FROM_NUMBER,
            body=body,
            received_at=BASE_TIME + timedelta(minutes=index),
        )

    turns = store.list_turns(
        tenant_id=TENANT_A,
        conversation_id=session.conversation_id,
    )

    assert [turn.sequence_number for turn in turns] == [1, 2, 3]
    assert [turn.body for turn in turns] == ["hola", "tienen bandeja?", "2 porfa"]


def test_only_open_status_is_written_by_m9_1_store_methods(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )
    store.append_turn_if_new(
        tenant_id=TENANT_A,
        conversation_id=session.conversation_id,
        message_sid="SM_STATUS_REACHABLE",
        from_number=FROM_NUMBER,
        body="hola",
        received_at=BASE_TIME,
    )

    current = store.get_session(
        tenant_id=TENANT_A,
        conversation_id=session.conversation_id,
    )

    assert current is not None
    assert current.status == "open"
    assert current.status not in {"draft_created", "expired", "failed"}


def test_mark_draft_created_sets_status_resulting_order_and_version(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    session = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )

    marked = store.mark_draft_created(
        tenant_id=TENANT_A,
        conversation_id=session.conversation_id,
        order_id="ord_resulting",
    )

    current = store.get_session(
        tenant_id=TENANT_A,
        conversation_id=session.conversation_id,
    )

    assert marked.status == "draft_created"
    assert marked.resulting_order_id == "ord_resulting"
    assert marked.version == session.version + 1
    assert marked.updated_at >= session.updated_at
    assert current is not None
    assert current.resulting_order_id == "ord_resulting"


def test_mark_draft_created_is_idempotent_for_same_order_id(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    session = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )

    first = store.mark_draft_created(
        tenant_id=TENANT_A,
        conversation_id=session.conversation_id,
        order_id="ord_same",
    )
    second = store.mark_draft_created(
        tenant_id=TENANT_A,
        conversation_id=session.conversation_id,
        order_id="ord_same",
    )

    assert first == second
    assert second.status == "draft_created"
    assert second.resulting_order_id == "ord_same"


def test_mark_draft_created_conflicts_for_different_order_id(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    session = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )
    store.mark_draft_created(
        tenant_id=TENANT_A,
        conversation_id=session.conversation_id,
        order_id="ord_first",
    )

    with pytest.raises(ValueError, match="different order"):
        store.mark_draft_created(
            tenant_id=TENANT_A,
            conversation_id=session.conversation_id,
            order_id="ord_second",
        )


def test_mark_draft_created_requires_tenant_scoped_session(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    session = store.get_or_create_open_session(
        tenant_id=TENANT_A,
        customer_phone=CUSTOMER_PHONE,
        received_at=BASE_TIME,
    )

    with pytest.raises(ValueError, match="not found"):
        store.mark_draft_created(
            tenant_id=TENANT_B,
            conversation_id=session.conversation_id,
            order_id="ord_resulting",
        )


def test_conversation_sessions_have_no_parse_status_fields() -> None:
    row_attributes = set(vars(ConversationSessionRow))

    assert "latest_parse_status" not in row_attributes
    assert "latest_parse_error" not in row_attributes


def test_store_does_not_import_parser_order_service_or_webhook() -> None:
    source = Path("src/duna_orders/storage/conversation_state.py").read_text()

    assert "ParsingService" not in source
    assert "ParserInterface" not in source
    assert "PROMPT_VERSION" not in source
    assert "OrderService" not in source
    assert "create_draft" not in source
    assert "webhook" not in source


@pytest.mark.live_postgres
def test_live_postgres_concurrent_open_session_creation_returns_one_session() -> None:
    store, engine, tenant_id = _live_store()
    customer_phone = f"whatsapp:+57{uuid4().hex[:10]}"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda _: store.get_or_create_open_session(
                        tenant_id=tenant_id,
                        customer_phone=customer_phone,
                        received_at=BASE_TIME,
                    ),
                    range(2),
                )
            )

        assert {session.conversation_id for session in results} == {
            results[0].conversation_id
        }
        with engine.connect() as connection:
            count = connection.execute(
                select(ConversationSessionRow)
                .where(ConversationSessionRow.tenant_id == tenant_id)
                .where(ConversationSessionRow.customer_phone == customer_phone)
                .where(ConversationSessionRow.status == "open")
            ).all()

        assert len(count) == 1
    finally:
        _cleanup_tenant(engine, tenant_id)
        engine.dispose()


@pytest.mark.live_postgres
def test_live_postgres_order_conversation_id_is_globally_unique() -> None:
    if not settings.database_url:
        pytest.skip("DATABASE_URL is required for live_postgres tests")

    upgrade(Config("alembic.ini"), "head")
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    storage = PostgresStorage(session_factory)
    tenant_a = f"tenant_live_order_a_{uuid4().hex}"
    tenant_b = f"tenant_live_order_b_{uuid4().hex}"
    conversation_id = f"conv_live_{uuid4().hex}"

    try:
        _cleanup_tenant(engine, tenant_a)
        _cleanup_tenant(engine, tenant_b)
        storage.create_order(
            _make_order(
                tenant_id=tenant_a,
                order_id=f"ord_live_a_{uuid4().hex}",
                conversation_id=conversation_id,
            )
        )

        with pytest.raises(IntegrityError):
            storage.create_order(
                _make_order(
                    tenant_id=tenant_b,
                    order_id=f"ord_live_b_{uuid4().hex}",
                    conversation_id=conversation_id,
                )
            )
    finally:
        _cleanup_tenant(engine, tenant_a)
        _cleanup_tenant(engine, tenant_b)
        engine.dispose()


@pytest.mark.live_postgres
def test_live_postgres_conversation_order_lookup_is_tenant_scoped() -> None:
    if not settings.database_url:
        pytest.skip("DATABASE_URL is required for live_postgres tests")

    upgrade(Config("alembic.ini"), "head")
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    storage = PostgresStorage(session_factory)
    lookup = PostgresConversationOrderLookup(session_factory)
    tenant_id = f"tenant_live_lookup_{uuid4().hex}"
    conversation_id = f"conv_lookup_{uuid4().hex}"
    order_id = f"ord_lookup_{uuid4().hex}"

    try:
        _cleanup_tenant(engine, tenant_id)
        storage.create_order(
            _make_order(
                tenant_id=tenant_id,
                order_id=order_id,
                conversation_id=conversation_id,
            )
        )

        found = lookup.get_order_by_conversation_id(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
        )
        wrong_tenant = lookup.get_order_by_conversation_id(
            tenant_id=f"wrong_{tenant_id}",
            conversation_id=conversation_id,
        )

        assert found is not None
        assert found.order_id == order_id
        assert wrong_tenant is None
    finally:
        _cleanup_tenant(engine, tenant_id)
        engine.dispose()


@pytest.mark.live_postgres
def test_live_postgres_concurrent_duplicate_turn_append_is_idempotent() -> None:
    store, engine, tenant_id = _live_store()
    message_sid = f"SM_{uuid4().hex}"

    try:
        session = store.get_or_create_open_session(
            tenant_id=tenant_id,
            customer_phone=CUSTOMER_PHONE,
            received_at=BASE_TIME,
        )

        def append_duplicate(_: int):
            return store.append_turn_if_new(
                tenant_id=tenant_id,
                conversation_id=session.conversation_id,
                message_sid=message_sid,
                from_number=FROM_NUMBER,
                body="hola",
                received_at=BASE_TIME,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(append_duplicate, range(2)))

        assert sum(result.appended for result in results) == 1
        turns = store.list_turns(
            tenant_id=tenant_id,
            conversation_id=session.conversation_id,
        )
        assert [turn.message_sid for turn in turns] == [message_sid]
    finally:
        _cleanup_tenant(engine, tenant_id)
        engine.dispose()


def _live_store():
    if not settings.database_url:
        pytest.skip("DATABASE_URL is required for live_postgres tests")

    upgrade(Config("alembic.ini"), "head")
    engine = make_engine(settings.database_url)
    tenant_id = f"tenant_live_conv_{uuid4().hex}"
    _cleanup_tenant(engine, tenant_id)

    return (
        PostgresConversationStateStore(make_session_factory(engine)),
        engine,
        tenant_id,
    )


def _cleanup_tenant(engine, tenant_id: str) -> None:
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            if "tenant_id" in table.c:
                connection.execute(table.delete().where(table.c.tenant_id == tenant_id))


def _make_order(
    *,
    tenant_id: str,
    order_id: str,
    conversation_id: str,
) -> Order:
    item = OrderItem(
        tenant_id=tenant_id,
        order_item_id=f"oit_{order_id}",
        order_id=order_id,
        product_id="prd_live_conversation",
        product_name_snapshot="Producto Live Conversation",
        unit_snapshot="unit",
        quantity=Decimal("1"),
        unit_price_snapshot=Decimal("1000"),
        line_total=Decimal("1000"),
        validation_status="ok",
    )
    return Order(
        tenant_id=tenant_id,
        order_id=order_id,
        conversation_id=conversation_id,
        raw_message="conversation-linked order",
        status="draft",
        items=[item],
        subtotal=Decimal("1000"),
        delivery_fee=Decimal("0"),
        packaging_fee=Decimal("0"),
        total=Decimal("1000"),
    )
