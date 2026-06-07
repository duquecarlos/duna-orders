from pathlib import Path

from duna_orders.storage.postgres_base import Base
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
        body_preview="Buenas, una bandeja paisa",
    )

    record = store.get_message("SM_NEW")

    assert created is True
    assert record is not None
    assert record.message_sid == "SM_NEW"
    assert record.tenant_id == DEFAULT_TEST_TENANT_ID
    assert record.from_number == "whatsapp:+573001112233"
    assert record.body_preview == "Buenas, una bandeja paisa"
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


def test_body_preview_is_trimmed_and_limited(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.try_record_message(
        message_sid="SM_LONG",
        tenant_id=DEFAULT_TEST_TENANT_ID,
        body_preview=f"  {'x' * 600}  ",
    )

    record = store.get_message("SM_LONG")

    assert record is not None
    assert record.body_preview == "x" * 500