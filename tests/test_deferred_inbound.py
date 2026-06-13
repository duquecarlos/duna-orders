from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.command import upgrade
from alembic.config import Config
from sqlalchemy.engine import Engine

from duna_orders.config import settings
from duna_orders.storage.deferred_inbound import PostgresDeferredInboundStore
from duna_orders.storage.postgres_base import Base
from duna_orders.storage.postgres_models import DeferredInboundRow
from duna_orders.storage.postgres_session import make_engine, make_session_factory
from tests.conftest import DEFAULT_TEST_TENANT_ID


RECEIVED_AT = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)


def _store(tmp_path: Path) -> PostgresDeferredInboundStore:
    database_path = tmp_path / "deferred_inbound.db"
    engine = make_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)

    return PostgresDeferredInboundStore(make_session_factory(engine))


def test_defer_message_inserts_pending_row(tmp_path: Path) -> None:
    store = _store(tmp_path)

    created = store.defer_message(
        message_sid="SM_NEW",
        tenant_id=DEFAULT_TEST_TENANT_ID,
        customer_key="+573001112233",
        from_number="whatsapp:+573001112233",
        raw_body="Buenas, una bandeja paisa",
        received_at=RECEIVED_AT,
    )

    records = store.list_pending_for_customer(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        customer_key="+573001112233",
    )

    assert created is True
    assert len(records) == 1

    record = records[0]
    assert record.message_sid == "SM_NEW"
    assert record.tenant_id == DEFAULT_TEST_TENANT_ID
    assert record.customer_key == "+573001112233"
    assert record.from_number == "whatsapp:+573001112233"
    assert record.raw_body == "Buenas, una bandeja paisa"
    assert record.received_at == RECEIVED_AT
    assert record.processed_at is None
    assert record.processing_started_at is None
    assert record.attempt_count == 0


def test_defer_message_duplicate_message_sid_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)

    first = store.defer_message(
        message_sid="SM_DUPLICATE",
        tenant_id=DEFAULT_TEST_TENANT_ID,
        customer_key="+573001112233",
        from_number="whatsapp:+573001112233",
        raw_body="first delivery",
        received_at=RECEIVED_AT,
    )
    second = store.defer_message(
        message_sid="SM_DUPLICATE",
        tenant_id=DEFAULT_TEST_TENANT_ID,
        customer_key="+573001112233",
        from_number="whatsapp:+573001112233",
        raw_body="retried delivery",
        received_at=RECEIVED_AT + timedelta(minutes=1),
    )

    records = store.list_pending_for_customer(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        customer_key="+573001112233",
    )

    assert first is True
    assert second is False
    assert len(records) == 1
    assert records[0].raw_body == "first delivery"


def test_has_pending_true_before_processed_false_after(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.defer_message(
        message_sid="SM_PENDING",
        tenant_id=DEFAULT_TEST_TENANT_ID,
        customer_key="+573001112233",
        from_number="whatsapp:+573001112233",
        raw_body="Buenas",
        received_at=RECEIVED_AT,
    )

    assert (
        store.has_pending(tenant_id=DEFAULT_TEST_TENANT_ID, customer_key="+573001112233")
        is True
    )

    store.mark_processed(message_sid="SM_PENDING")

    assert (
        store.has_pending(tenant_id=DEFAULT_TEST_TENANT_ID, customer_key="+573001112233")
        is False
    )


def test_list_pending_for_customer_filters_by_tenant_and_customer(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.defer_message(
        message_sid="SM_MATCH",
        tenant_id=DEFAULT_TEST_TENANT_ID,
        customer_key="+573001112233",
        from_number="whatsapp:+573001112233",
        raw_body="matching customer",
        received_at=RECEIVED_AT,
    )
    store.defer_message(
        message_sid="SM_OTHER_CUSTOMER",
        tenant_id=DEFAULT_TEST_TENANT_ID,
        customer_key="+573009998877",
        from_number="whatsapp:+573009998877",
        raw_body="other customer, same tenant",
        received_at=RECEIVED_AT,
    )
    store.defer_message(
        message_sid="SM_OTHER_TENANT",
        tenant_id="tenant_other",
        customer_key="+573001112233",
        from_number="whatsapp:+573001112233",
        raw_body="same customer, other tenant",
        received_at=RECEIVED_AT,
    )

    records = store.list_pending_for_customer(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        customer_key="+573001112233",
    )

    assert [record.message_sid for record in records] == ["SM_MATCH"]


def test_list_pending_for_tenant_spans_customers_and_excludes_other_tenants_and_processed(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    store.defer_message(
        message_sid="SM_TENANT_CUSTOMER_A",
        tenant_id=DEFAULT_TEST_TENANT_ID,
        customer_key="+573001112233",
        from_number="whatsapp:+573001112233",
        raw_body="customer a",
        received_at=RECEIVED_AT,
    )
    store.defer_message(
        message_sid="SM_TENANT_CUSTOMER_B",
        tenant_id=DEFAULT_TEST_TENANT_ID,
        customer_key="+573009998877",
        from_number="whatsapp:+573009998877",
        raw_body="customer b, same tenant",
        received_at=RECEIVED_AT + timedelta(minutes=1),
    )
    store.defer_message(
        message_sid="SM_TENANT_PROCESSED",
        tenant_id=DEFAULT_TEST_TENANT_ID,
        customer_key="+573001112233",
        from_number="whatsapp:+573001112233",
        raw_body="already drained",
        received_at=RECEIVED_AT + timedelta(minutes=2),
    )
    store.mark_processed(message_sid="SM_TENANT_PROCESSED")
    store.defer_message(
        message_sid="SM_OTHER_TENANT",
        tenant_id="tenant_other",
        customer_key="+573001112233",
        from_number="whatsapp:+573001112233",
        raw_body="other tenant",
        received_at=RECEIVED_AT,
    )

    records = store.list_pending_for_tenant(tenant_id=DEFAULT_TEST_TENANT_ID)

    assert [record.message_sid for record in records] == [
        "SM_TENANT_CUSTOMER_A",
        "SM_TENANT_CUSTOMER_B",
    ]


def test_list_pending_for_tenant_respects_limit(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.defer_message(
        message_sid="SM_LIMIT_A",
        tenant_id=DEFAULT_TEST_TENANT_ID,
        customer_key="+573001112233",
        from_number="whatsapp:+573001112233",
        raw_body="first",
        received_at=RECEIVED_AT,
    )
    store.defer_message(
        message_sid="SM_LIMIT_B",
        tenant_id=DEFAULT_TEST_TENANT_ID,
        customer_key="+573009998877",
        from_number="whatsapp:+573009998877",
        raw_body="second",
        received_at=RECEIVED_AT + timedelta(minutes=1),
    )

    records = store.list_pending_for_tenant(tenant_id=DEFAULT_TEST_TENANT_ID, limit=1)

    assert [record.message_sid for record in records] == ["SM_LIMIT_A"]


def test_list_pending_for_customer_excludes_processed_rows(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.defer_message(
        message_sid="SM_PENDING",
        tenant_id=DEFAULT_TEST_TENANT_ID,
        customer_key="+573001112233",
        from_number="whatsapp:+573001112233",
        raw_body="still pending",
        received_at=RECEIVED_AT,
    )
    store.defer_message(
        message_sid="SM_PROCESSED",
        tenant_id=DEFAULT_TEST_TENANT_ID,
        customer_key="+573001112233",
        from_number="whatsapp:+573001112233",
        raw_body="already drained",
        received_at=RECEIVED_AT + timedelta(minutes=1),
    )
    store.mark_processed(message_sid="SM_PROCESSED")

    records = store.list_pending_for_customer(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        customer_key="+573001112233",
    )

    assert [record.message_sid for record in records] == ["SM_PENDING"]


def test_list_pending_for_customer_orders_by_received_then_deferred_then_sid(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    session = store._session_factory()

    try:
        session.add_all(
            [
                DeferredInboundRow(
                    message_sid="SM_C",
                    tenant_id=DEFAULT_TEST_TENANT_ID,
                    customer_key="+573001112233",
                    from_number="whatsapp:+573001112233",
                    raw_body="c",
                    received_at=RECEIVED_AT,
                    deferred_at=RECEIVED_AT + timedelta(seconds=2),
                    attempt_count=0,
                ),
                DeferredInboundRow(
                    message_sid="SM_B",
                    tenant_id=DEFAULT_TEST_TENANT_ID,
                    customer_key="+573001112233",
                    from_number="whatsapp:+573001112233",
                    raw_body="b",
                    received_at=RECEIVED_AT,
                    deferred_at=RECEIVED_AT + timedelta(seconds=1),
                    attempt_count=0,
                ),
                DeferredInboundRow(
                    message_sid="SM_A",
                    tenant_id=DEFAULT_TEST_TENANT_ID,
                    customer_key="+573001112233",
                    from_number="whatsapp:+573001112233",
                    raw_body="a",
                    received_at=RECEIVED_AT - timedelta(minutes=1),
                    deferred_at=RECEIVED_AT,
                    attempt_count=0,
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    records = store.list_pending_for_customer(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        customer_key="+573001112233",
    )

    assert [record.message_sid for record in records] == ["SM_A", "SM_B", "SM_C"]


def test_mark_processing_started_increments_attempt_count(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.defer_message(
        message_sid="SM_ATTEMPT",
        tenant_id=DEFAULT_TEST_TENANT_ID,
        customer_key="+573001112233",
        from_number="whatsapp:+573001112233",
        raw_body="retry me",
        received_at=RECEIVED_AT,
    )

    first = store.mark_processing_started(message_sid="SM_ATTEMPT")
    second = store.mark_processing_started(message_sid="SM_ATTEMPT")

    records = store.list_pending_for_customer(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        customer_key="+573001112233",
    )

    assert first is True
    assert second is True
    assert records[0].attempt_count == 2
    assert records[0].processing_started_at is not None


def test_mark_processed_sets_processed_at_and_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.defer_message(
        message_sid="SM_DONE",
        tenant_id=DEFAULT_TEST_TENANT_ID,
        customer_key="+573001112233",
        from_number="whatsapp:+573001112233",
        raw_body="done",
        received_at=RECEIVED_AT,
    )

    first = store.mark_processed(message_sid="SM_DONE")
    second = store.mark_processed(message_sid="SM_DONE")

    assert first is True
    assert second is False
    assert (
        store.has_pending(tenant_id=DEFAULT_TEST_TENANT_ID, customer_key="+573001112233")
        is False
    )


def test_mark_processing_started_and_mark_processed_return_false_for_missing_sid(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    assert store.mark_processing_started(message_sid="SM_MISSING") is False
    assert store.mark_processed(message_sid="SM_MISSING") is False


def test_tenant_isolation_for_has_pending_and_list_pending(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.defer_message(
        message_sid="SM_TENANT_A",
        tenant_id=DEFAULT_TEST_TENANT_ID,
        customer_key="+573001112233",
        from_number="whatsapp:+573001112233",
        raw_body="tenant a message",
        received_at=RECEIVED_AT,
    )

    assert store.has_pending(tenant_id="tenant_other", customer_key="+573001112233") is False
    assert (
        store.list_pending_for_customer(tenant_id="tenant_other", customer_key="+573001112233")
        == []
    )


# --- live_postgres production-store tests ---


def _live_store() -> tuple[PostgresDeferredInboundStore, Engine, str]:
    if not settings.database_url:
        pytest.skip("DATABASE_URL is required for live_postgres tests")

    upgrade(Config("alembic.ini"), "head")
    engine = make_engine(settings.database_url)
    tenant_id = f"tenant_live_deferred_{uuid4().hex}"
    _cleanup_tenant(engine, tenant_id)

    return (
        PostgresDeferredInboundStore(make_session_factory(engine)),
        engine,
        tenant_id,
    )


def _cleanup_tenant(engine: Engine, tenant_id: str) -> None:
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            if "tenant_id" in table.c:
                connection.execute(table.delete().where(table.c.tenant_id == tenant_id))


@pytest.mark.live_postgres
def test_live_defer_message_pending_then_processed_lifecycle() -> None:
    store, engine, tenant_id = _live_store()

    try:
        customer_key = "+573001112233"
        message_sid = f"SM_LIVE_{uuid4().hex}"

        created = store.defer_message(
            message_sid=message_sid,
            tenant_id=tenant_id,
            customer_key=customer_key,
            from_number="whatsapp:+573001112233",
            raw_body="live smoke message",
            received_at=RECEIVED_AT,
        )

        assert created is True
        assert store.has_pending(tenant_id=tenant_id, customer_key=customer_key) is True

        records = store.list_pending_for_customer(tenant_id=tenant_id, customer_key=customer_key)
        assert [record.message_sid for record in records] == [message_sid]

        assert store.mark_processing_started(message_sid=message_sid) is True
        assert store.mark_processed(message_sid=message_sid) is True
        assert store.has_pending(tenant_id=tenant_id, customer_key=customer_key) is False
    finally:
        _cleanup_tenant(engine, tenant_id)
        engine.dispose()
