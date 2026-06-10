from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from duna_orders.domain.models import utc_now
from duna_orders.storage.outbound_messages import (
    ORDER_CONFIRMED_ACK,
    PostgresOutboundAcknowledgementStore,
)
from duna_orders.storage.postgres_base import Base
from duna_orders.storage.postgres_models import OutboundMessageRow
from duna_orders.storage.postgres_session import make_engine, make_session_factory
from tests.conftest import DEFAULT_TEST_TENANT_ID


ORDER_ID = "ord_outbound"


def _store(tmp_path: Path) -> PostgresOutboundAcknowledgementStore:
    database_path = tmp_path / "outbound_messages.db"
    engine = make_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    return PostgresOutboundAcknowledgementStore(make_session_factory(engine))


def _claim(
    store: PostgresOutboundAcknowledgementStore,
    *,
    tenant_id: str = DEFAULT_TEST_TENANT_ID,
    order_id: str = ORDER_ID,
    retry_failed: bool = False,
):
    return store.claim_order_acknowledgement_for_send(
        tenant_id=tenant_id,
        order_id=order_id,
        acknowledgement_type=ORDER_CONFIRMED_ACK,
        to_number="whatsapp:+573001112233",
        from_number="whatsapp:+15551234567",
        body="Hola, tu pedido quedó confirmado.",
        requested_by="operator",
        retry_failed=retry_failed,
    )


def test_first_claim_creates_sending_row_with_attempt_count_one(tmp_path: Path) -> None:
    store = _store(tmp_path)

    result = _claim(store)

    assert result.claimed_for_send is True
    assert result.reason == "created"
    assert result.acknowledgement.status == "sending"
    assert result.acknowledgement.attempt_count == 1
    assert result.acknowledgement.provider == "twilio"


def test_duplicate_first_claim_returns_existing_row_without_claim(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _claim(store)

    second = _claim(store)

    assert second.claimed_for_send is False
    assert second.reason == "suppressed_in_progress"
    assert second.acknowledgement.outbound_message_id == first.acknowledgement.outbound_message_id
    assert second.acknowledgement.attempt_count == 1


def test_send_requested_suppresses_claim(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _claim(store)
    _set_status(store, first.acknowledgement.outbound_message_id, "send_requested")

    second = _claim(store)

    assert second.claimed_for_send is False
    assert second.reason == "suppressed_in_progress"


def test_sending_suppresses_claim(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _claim(store)

    second = _claim(store)

    assert first.acknowledgement.status == "sending"
    assert second.claimed_for_send is False
    assert second.reason == "suppressed_in_progress"


def test_sent_suppresses_claim(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _claim(store)
    store.mark_sent(
        outbound_message_id=first.acknowledgement.outbound_message_id,
        provider_message_id="SM_SENT",
    )

    second = _claim(store)

    assert second.claimed_for_send is False
    assert second.reason == "suppressed_sent"


def test_unknown_suppresses_claim(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _claim(store)
    store.mark_unknown(
        outbound_message_id=first.acknowledgement.outbound_message_id,
        error_code="timeout",
        error_message="provider timeout",
    )

    second = _claim(store, retry_failed=True)

    assert second.claimed_for_send is False
    assert second.reason == "suppressed_unknown"


def test_failed_suppresses_claim_without_retry(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _claim(store)
    store.mark_failed(
        outbound_message_id=first.acknowledgement.outbound_message_id,
        error_code="provider_error",
        error_message="provider rejected message",
    )

    second = _claim(store)

    assert second.claimed_for_send is False
    assert second.reason == "suppressed_failed_without_retry"
    assert second.acknowledgement.status == "failed"


def test_failed_retry_reuses_row_and_increments_attempt_count(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _claim(store)
    store.mark_failed(
        outbound_message_id=first.acknowledgement.outbound_message_id,
        error_code="provider_error",
        error_message="provider rejected message",
    )

    retry = _claim(store, retry_failed=True)

    assert retry.claimed_for_send is True
    assert retry.reason == "retry_claimed"
    assert retry.acknowledgement.outbound_message_id == first.acknowledgement.outbound_message_id
    assert retry.acknowledgement.status == "sending"
    assert retry.acknowledgement.attempt_count == 2
    assert retry.acknowledgement.last_error_code is None
    assert retry.acknowledgement.last_error_message is None


def test_second_failed_retry_claim_is_suppressed_after_first_claims(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _claim(store)
    store.mark_failed(
        outbound_message_id=first.acknowledgement.outbound_message_id,
        error_code="provider_error",
        error_message="provider rejected message",
    )

    retry = _claim(store, retry_failed=True)
    second_retry = _claim(store, retry_failed=True)

    assert retry.claimed_for_send is True
    assert second_retry.claimed_for_send is False
    assert second_retry.reason == "suppressed_in_progress"
    assert second_retry.acknowledgement.attempt_count == 2


def test_mark_sent_stores_provider_id_and_sent_at(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _claim(store)
    store.mark_failed(
        outbound_message_id=first.acknowledgement.outbound_message_id,
        error_code="old_error",
        error_message="old error",
    )
    retry = _claim(store, retry_failed=True)

    sent = store.mark_sent(
        outbound_message_id=retry.acknowledgement.outbound_message_id,
        provider_message_id="SM_SENT",
    )

    assert sent.status == "sent"
    assert sent.provider_message_id == "SM_SENT"
    assert sent.sent_at is not None
    assert sent.last_error_code is None
    assert sent.last_error_message is None


def test_mark_sent_refuses_to_overwrite_failed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _claim(store)
    failed = store.mark_failed(
        outbound_message_id=first.acknowledgement.outbound_message_id,
        error_code="provider_error",
        error_message="provider rejected message",
    )

    with pytest.raises(ValueError, match="must be sending"):
        store.mark_sent(
            outbound_message_id=first.acknowledgement.outbound_message_id,
            provider_message_id="SM_SENT",
        )

    current = _get_by_id(store, first.acknowledgement.outbound_message_id)

    assert current.status == "failed"
    assert current.provider_message_id is None
    assert current.last_error_code == failed.last_error_code
    assert current.last_error_message == failed.last_error_message


def test_mark_failed_stores_error_code_and_message(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _claim(store)

    failed = store.mark_failed(
        outbound_message_id=first.acknowledgement.outbound_message_id,
        error_code="provider_error",
        error_message="provider rejected message",
    )

    assert failed.status == "failed"
    assert failed.last_error_code == "provider_error"
    assert failed.last_error_message == "provider rejected message"


def test_mark_failed_refuses_to_overwrite_sent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _claim(store)
    sent = store.mark_sent(
        outbound_message_id=first.acknowledgement.outbound_message_id,
        provider_message_id="SM_SENT",
    )

    with pytest.raises(ValueError, match="must be sending"):
        store.mark_failed(
            outbound_message_id=first.acknowledgement.outbound_message_id,
            error_code="late_error",
            error_message="late provider error",
        )

    current = _get_by_id(store, first.acknowledgement.outbound_message_id)

    assert current.status == "sent"
    assert current.provider_message_id == sent.provider_message_id
    assert current.last_error_code is None
    assert current.last_error_message is None


def test_mark_unknown_stores_error_and_suppresses_future_claims(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _claim(store)

    unknown = store.mark_unknown(
        outbound_message_id=first.acknowledgement.outbound_message_id,
        error_code="timeout",
        error_message="provider response unknown",
    )
    second = _claim(store, retry_failed=True)

    assert unknown.status == "unknown"
    assert unknown.last_error_code == "timeout"
    assert unknown.last_error_message == "provider response unknown"
    assert second.claimed_for_send is False
    assert second.reason == "suppressed_unknown"


def test_mark_unknown_refuses_to_overwrite_sent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _claim(store)
    sent = store.mark_sent(
        outbound_message_id=first.acknowledgement.outbound_message_id,
        provider_message_id="SM_SENT",
    )

    with pytest.raises(ValueError, match="must be sending"):
        store.mark_unknown(
            outbound_message_id=first.acknowledgement.outbound_message_id,
            error_code="timeout",
            error_message="late timeout",
        )

    current = _get_by_id(store, first.acknowledgement.outbound_message_id)

    assert current.status == "sent"
    assert current.provider_message_id == sent.provider_message_id
    assert current.last_error_code is None
    assert current.last_error_message is None


def test_failed_retry_transitions_to_sending_then_mark_sent_works(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _claim(store)
    store.mark_failed(
        outbound_message_id=first.acknowledgement.outbound_message_id,
        error_code="provider_error",
        error_message="provider rejected message",
    )

    retry = _claim(store, retry_failed=True)
    sent = store.mark_sent(
        outbound_message_id=retry.acknowledgement.outbound_message_id,
        provider_message_id="SM_RETRY_SENT",
    )

    assert retry.acknowledgement.status == "sending"
    assert sent.status == "sent"
    assert sent.provider_message_id == "SM_RETRY_SENT"
    assert sent.attempt_count == 2


def test_get_for_order_acknowledgement_returns_row_by_tenant_order_and_type(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    first = _claim(store)

    found = store.get_for_order_acknowledgement(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        order_id=ORDER_ID,
        acknowledgement_type=ORDER_CONFIRMED_ACK,
    )
    missing = store.get_for_order_acknowledgement(
        tenant_id="other-tenant",
        order_id=ORDER_ID,
        acknowledgement_type=ORDER_CONFIRMED_ACK,
    )

    assert found == first.acknowledgement
    assert missing is None


def test_same_order_and_type_do_not_collide_across_tenants(tmp_path: Path) -> None:
    store = _store(tmp_path)

    first = _claim(store, tenant_id=DEFAULT_TEST_TENANT_ID)
    second = _claim(store, tenant_id="other-tenant")

    assert first.claimed_for_send is True
    assert second.claimed_for_send is True
    assert first.acknowledgement.outbound_message_id != second.acknowledgement.outbound_message_id


def test_storage_interface_is_not_extended_for_outbound_messages() -> None:
    from duna_orders.storage.base import StorageInterface

    assert not hasattr(StorageInterface, "claim_order_acknowledgement_for_send")
    assert not hasattr(StorageInterface, "mark_sent")
    assert not hasattr(StorageInterface, "mark_failed")
    assert not hasattr(StorageInterface, "mark_unknown")


def test_order_confirmation_path_has_no_outbound_dependency() -> None:
    orders_source = (Path("src/duna_orders/services/orders.py")).read_text()
    confirmation_source = (Path("src/duna_orders/storage/order_confirmation.py")).read_text()

    assert "outbound" not in orders_source.casefold()
    assert "acknowledgement" not in orders_source.casefold()
    assert "outbound" not in confirmation_source.casefold()
    assert "acknowledgement" not in confirmation_source.casefold()


def test_outbound_store_has_no_parser_prompt_dependency() -> None:
    source = (Path("src/duna_orders/storage/outbound_messages.py")).read_text()

    assert "PROMPT_VERSION" not in source
    assert "parsing" not in source


def _set_status(
    store: PostgresOutboundAcknowledgementStore,
    outbound_message_id: str,
    status: str,
) -> None:
    session = store._session_factory()
    try:
        row = session.scalar(
            select(OutboundMessageRow).where(
                OutboundMessageRow.outbound_message_id == outbound_message_id
            )
        )
        assert row is not None
        row.status = status
        row.updated_at = utc_now()
        session.commit()
    finally:
        session.close()


def _get_by_id(
    store: PostgresOutboundAcknowledgementStore,
    outbound_message_id: str,
):
    session = store._session_factory()
    try:
        row = session.scalar(
            select(OutboundMessageRow).where(
                OutboundMessageRow.outbound_message_id == outbound_message_id
            )
        )
        assert row is not None
        return row
    finally:
        session.close()
