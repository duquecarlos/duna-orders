from pathlib import Path
from datetime import datetime, timezone

from duna_orders.storage.postgres_base import Base
from duna_orders.storage.postgres_models import ProcessedMessageRow
from duna_orders.storage.postgres_session import make_engine, make_session_factory
from duna_orders.storage.processed_messages import PostgresProcessedMessageStore
from tests.conftest import DEFAULT_TEST_TENANT_ID


def _store(tmp_path: Path) -> PostgresProcessedMessageStore:
    database_path = tmp_path / "processed_messages.db"
    engine = make_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)

    return PostgresProcessedMessageStore(make_session_factory(engine))


def test_try_record_message_returns_true_for_new_message(tmp_path: Path) -> None:
    store = _store(tmp_path)

    created = store.try_record_message(
        message_sid="SM_NEW",
        tenant_id=DEFAULT_TEST_TENANT_ID,
        from_number="whatsapp:+573001112233",
        raw_body="Buenas, una bandeja paisa",
    )

    record = store.get_message("SM_NEW")

    assert created is True
    assert record is not None
    assert record.message_sid == "SM_NEW"
    assert record.tenant_id == DEFAULT_TEST_TENANT_ID
    assert record.from_number == "whatsapp:+573001112233"
    assert record.raw_body == "Buenas, una bandeja paisa"
    assert record.resulting_order_id is None


def test_try_record_message_returns_false_for_duplicate_message_sid(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    first = store.try_record_message(
        message_sid="SM_DUPLICATE",
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )
    second = store.try_record_message(
        message_sid="SM_DUPLICATE",
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )

    assert first is True
    assert second is False


def test_mark_order_created_links_resulting_order_id(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.try_record_message(
        message_sid="SM_ORDER",
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )

    store.mark_order_created(message_sid="SM_ORDER", order_id="ord_test")

    record = store.get_message("SM_ORDER")

    assert record is not None
    assert record.resulting_order_id == "ord_test"


def test_get_message_for_order_uses_resulting_order_id_link(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.try_record_message(
        message_sid="SM_OTHER",
        tenant_id=DEFAULT_TEST_TENANT_ID,
        raw_body="Older unrelated message",
    )
    store.try_record_message(
        message_sid="SM_TARGET",
        tenant_id=DEFAULT_TEST_TENANT_ID,
        from_number="whatsapp:+573001112233",
        raw_body="Buenas, dos bandejas paisas sin aguacate",
    )
    store.mark_order_created(message_sid="SM_TARGET", order_id="ord_target")

    record = store.get_message_for_order(
        order_id="ord_target",
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )

    assert record is not None
    assert record.message_sid == "SM_TARGET"
    assert record.raw_body == "Buenas, dos bandejas paisas sin aguacate"
    assert record.from_number == "whatsapp:+573001112233"
    assert record.resulting_order_id == "ord_target"


def test_get_message_for_order_respects_tenant_scope(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.try_record_message(
        message_sid="SM_TARGET",
        tenant_id=DEFAULT_TEST_TENANT_ID,
        raw_body="Buenas, una limonada",
    )
    store.mark_order_created(message_sid="SM_TARGET", order_id="ord_target")

    record = store.get_message_for_order(
        order_id="ord_target",
        tenant_id="tenant_other",
    )

    assert record is None


def test_get_message_for_order_does_not_guess_without_resulting_order_id(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    store.try_record_message(
        message_sid="SM_UNLINKED",
        tenant_id=DEFAULT_TEST_TENANT_ID,
        raw_body="Potentially similar raw message",
    )

    record = store.get_message_for_order(
        order_id="ord_unlinked",
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )

    assert record is None


def test_list_messages_with_resulting_order_returns_tenant_links_newest_first(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    _insert_processed_message(
        store,
        message_sid="SM_OLD",
        tenant_id=DEFAULT_TEST_TENANT_ID,
        received_at=datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc),
        raw_body="Older linked message",
        resulting_order_id="ord_old",
    )
    _insert_processed_message(
        store,
        message_sid="SM_NEW",
        tenant_id=DEFAULT_TEST_TENANT_ID,
        received_at=datetime(2026, 6, 8, 12, 5, tzinfo=timezone.utc),
        from_number="whatsapp:+573001112233",
        raw_body="Newer linked message",
        resulting_order_id="ord_new",
    )
    _insert_processed_message(
        store,
        message_sid="SM_UNLINKED",
        tenant_id=DEFAULT_TEST_TENANT_ID,
        received_at=datetime(2026, 6, 8, 12, 10, tzinfo=timezone.utc),
        raw_body="Unlinked message",
        resulting_order_id=None,
    )
    _insert_processed_message(
        store,
        message_sid="SM_OTHER_TENANT",
        tenant_id="tenant_other",
        received_at=datetime(2026, 6, 8, 12, 15, tzinfo=timezone.utc),
        raw_body="Other tenant message",
        resulting_order_id="ord_other",
    )

    records = store.list_messages_with_resulting_order(
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )

    assert [record.message_sid for record in records] == ["SM_NEW", "SM_OLD"]
    assert records[0].raw_body == "Newer linked message"
    assert records[0].from_number == "whatsapp:+573001112233"
    assert records[0].resulting_order_id == "ord_new"


def test_raw_body_preserves_full_untrimmed_message(tmp_path: Path) -> None:
    store = _store(tmp_path)
    raw_body = f"  {'x' * 600}  "

    store.try_record_message(
        message_sid="SM_LONG",
        tenant_id=DEFAULT_TEST_TENANT_ID,
        raw_body=raw_body,
    )

    record = store.get_message("SM_LONG")

    assert record is not None
    assert record.raw_body == raw_body


def _insert_processed_message(
    store: PostgresProcessedMessageStore,
    *,
    message_sid: str,
    tenant_id: str,
    received_at: datetime,
    from_number: str | None = None,
    raw_body: str | None = None,
    resulting_order_id: str | None = None,
) -> None:
    session = store._session_factory()

    try:
        session.add(
            ProcessedMessageRow(
                message_sid=message_sid,
                tenant_id=tenant_id,
                received_at=received_at,
                from_number=from_number,
                raw_body=raw_body,
                resulting_order_id=resulting_order_id,
            )
        )
        session.commit()
    finally:
        session.close()
