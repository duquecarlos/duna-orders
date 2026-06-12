"""Tests for the M9.6C production customer-claim store foundation.

This store is intentionally unwired: it is not used by
`ConversationAdvancementService.advance(...)`, the webhook, the UI, or the
parser. These tests exercise the store directly against the real
`conversation_customer_claims` table (Alembic-managed, part of
`Base.metadata`), following the live_postgres conventions used by
`tests/test_conversation_state_store.py`.
"""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from uuid import uuid4

import pytest
from alembic.command import upgrade
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Engine

from duna_orders.config import settings
from duna_orders.storage.conversation_customer_claims import (
    PostgresConversationCustomerClaimStore,
    normalize_customer_claim_key,
)
from duna_orders.storage.postgres_base import Base
from duna_orders.storage.postgres_session import make_engine, make_session_factory


# --- pure helper tests ---


def test_normalize_customer_claim_key_is_deterministic() -> None:
    first = normalize_customer_claim_key("tenant-a", "whatsapp:+57 300 111 2233")
    second = normalize_customer_claim_key("tenant-a", "whatsapp:+57 300 111 2233")

    assert first == second


def test_normalize_customer_claim_key_normalizes_equivalent_phone_formats() -> None:
    spaced = normalize_customer_claim_key("tenant-a", "whatsapp:+57 300 111 2233")
    dashed = normalize_customer_claim_key("tenant-a", "whatsapp:+57-300-111-2233")
    plain = normalize_customer_claim_key("tenant-a", "whatsapp:+573001112233")

    assert spaced == dashed == plain


def test_normalize_customer_claim_key_differs_for_different_phone_numbers() -> None:
    key_a = normalize_customer_claim_key("tenant-a", "whatsapp:+573001112233")
    key_b = normalize_customer_claim_key("tenant-a", "whatsapp:+573009998877")

    assert key_a != key_b


def test_normalize_customer_claim_key_does_not_embed_tenant_id() -> None:
    key_for_tenant_a = normalize_customer_claim_key("tenant-a", "whatsapp:+573001112233")
    key_for_tenant_b = normalize_customer_claim_key("tenant-b", "whatsapp:+573001112233")

    assert key_for_tenant_a == key_for_tenant_b
    assert "tenant-a" not in key_for_tenant_a
    assert "tenant-b" not in key_for_tenant_a


# --- live_postgres production-store tests ---


def _live_store() -> tuple[PostgresConversationCustomerClaimStore, Engine, str]:
    if not settings.database_url:
        pytest.skip("DATABASE_URL is required for live_postgres tests")

    upgrade(Config("alembic.ini"), "head")
    engine = make_engine(settings.database_url)
    tenant_id = f"tenant_live_claim_{uuid4().hex}"
    _cleanup_tenant(engine, tenant_id)

    return (
        PostgresConversationCustomerClaimStore(make_session_factory(engine)),
        engine,
        tenant_id,
    )


def _cleanup_tenant(engine: Engine, tenant_id: str) -> None:
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            if "tenant_id" in table.c:
                connection.execute(table.delete().where(table.c.tenant_id == tenant_id))


def _read_claim(engine: Engine, *, tenant_id: str, customer_key: str):
    with engine.connect() as connection:
        return connection.execute(
            text(
                """
                SELECT holder_id, lease_expires_at
                FROM conversation_customer_claims
                WHERE tenant_id = :tenant_id AND customer_key = :customer_key
                """
            ),
            {"tenant_id": tenant_id, "customer_key": customer_key},
        ).first()


@pytest.mark.live_postgres
def test_try_acquire_succeeds_when_no_claim_exists() -> None:
    store, engine, tenant_id = _live_store()
    customer_key = f"whatsapp:+57{uuid4().hex[:10]}"

    try:
        acquired = store.try_acquire(
            tenant_id=tenant_id,
            customer_key=customer_key,
            holder_id="holder-a",
            lease_duration=timedelta(seconds=30),
        )
        assert acquired is True

        row = _read_claim(engine, tenant_id=tenant_id, customer_key=customer_key)
        assert row is not None
        assert row.holder_id == "holder-a"
    finally:
        _cleanup_tenant(engine, tenant_id)
        engine.dispose()


@pytest.mark.live_postgres
def test_try_acquire_fails_when_live_lease_is_held() -> None:
    store, engine, tenant_id = _live_store()
    customer_key = f"whatsapp:+57{uuid4().hex[:10]}"

    try:
        first = store.try_acquire(
            tenant_id=tenant_id,
            customer_key=customer_key,
            holder_id="holder-a",
            lease_duration=timedelta(seconds=30),
        )
        assert first is True

        second = store.try_acquire(
            tenant_id=tenant_id,
            customer_key=customer_key,
            holder_id="holder-b",
            lease_duration=timedelta(seconds=30),
        )
        assert second is False

        row = _read_claim(engine, tenant_id=tenant_id, customer_key=customer_key)
        assert row is not None
        assert row.holder_id == "holder-a"
    finally:
        _cleanup_tenant(engine, tenant_id)
        engine.dispose()


@pytest.mark.live_postgres
def test_expired_lease_can_be_taken_over_and_original_holder_can_no_longer_renew() -> None:
    store, engine, tenant_id = _live_store()
    customer_key = f"whatsapp:+57{uuid4().hex[:10]}"

    try:
        seeded = store.try_acquire(
            tenant_id=tenant_id,
            customer_key=customer_key,
            holder_id="stale-holder",
            lease_duration=timedelta(seconds=1),
        )
        assert seeded is True

        time.sleep(1.2)

        took_over = store.try_acquire(
            tenant_id=tenant_id,
            customer_key=customer_key,
            holder_id="new-holder",
            lease_duration=timedelta(seconds=30),
        )
        assert took_over is True

        row = _read_claim(engine, tenant_id=tenant_id, customer_key=customer_key)
        assert row is not None
        assert row.holder_id == "new-holder"

        stale_renew = store.renew(
            tenant_id=tenant_id,
            customer_key=customer_key,
            holder_id="stale-holder",
            lease_duration=timedelta(seconds=30),
        )
        assert stale_renew is False
    finally:
        _cleanup_tenant(engine, tenant_id)
        engine.dispose()


@pytest.mark.live_postgres
def test_release_succeeds_only_for_matching_holder() -> None:
    store, engine, tenant_id = _live_store()
    customer_key = f"whatsapp:+57{uuid4().hex[:10]}"

    try:
        acquired = store.try_acquire(
            tenant_id=tenant_id,
            customer_key=customer_key,
            holder_id="holder-a",
            lease_duration=timedelta(seconds=30),
        )
        assert acquired is True

        mismatched = store.release(
            tenant_id=tenant_id,
            customer_key=customer_key,
            holder_id="holder-b",
        )
        assert mismatched is False

        row = _read_claim(engine, tenant_id=tenant_id, customer_key=customer_key)
        assert row is not None
        assert row.holder_id == "holder-a"

        released = store.release(
            tenant_id=tenant_id,
            customer_key=customer_key,
            holder_id="holder-a",
        )
        assert released is True

        row = _read_claim(engine, tenant_id=tenant_id, customer_key=customer_key)
        assert row is None
    finally:
        _cleanup_tenant(engine, tenant_id)
        engine.dispose()


@pytest.mark.live_postgres
def test_renew_succeeds_for_matching_holder_and_extends_lease() -> None:
    store, engine, tenant_id = _live_store()
    customer_key = f"whatsapp:+57{uuid4().hex[:10]}"

    try:
        acquired = store.try_acquire(
            tenant_id=tenant_id,
            customer_key=customer_key,
            holder_id="holder-a",
            lease_duration=timedelta(seconds=1),
        )
        assert acquired is True

        row_before = _read_claim(engine, tenant_id=tenant_id, customer_key=customer_key)
        assert row_before is not None

        renewed = store.renew(
            tenant_id=tenant_id,
            customer_key=customer_key,
            holder_id="holder-a",
            lease_duration=timedelta(seconds=30),
        )
        assert renewed is True

        row_after = _read_claim(engine, tenant_id=tenant_id, customer_key=customer_key)
        assert row_after is not None
        assert row_after.holder_id == "holder-a"
        assert row_after.lease_expires_at > row_before.lease_expires_at
    finally:
        _cleanup_tenant(engine, tenant_id)
        engine.dispose()


@pytest.mark.live_postgres
def test_renew_returns_false_for_holder_mismatch() -> None:
    store, engine, tenant_id = _live_store()
    customer_key = f"whatsapp:+57{uuid4().hex[:10]}"

    try:
        acquired = store.try_acquire(
            tenant_id=tenant_id,
            customer_key=customer_key,
            holder_id="holder-a",
            lease_duration=timedelta(seconds=30),
        )
        assert acquired is True

        renewed = store.renew(
            tenant_id=tenant_id,
            customer_key=customer_key,
            holder_id="holder-b",
            lease_duration=timedelta(seconds=30),
        )
        assert renewed is False

        row = _read_claim(engine, tenant_id=tenant_id, customer_key=customer_key)
        assert row is not None
        assert row.holder_id == "holder-a"
    finally:
        _cleanup_tenant(engine, tenant_id)
        engine.dispose()


@pytest.mark.live_postgres
def test_same_customer_claim_serializes_concurrent_workers() -> None:
    store, engine, tenant_id = _live_store()
    customer_key = f"whatsapp:+57{uuid4().hex[:10]}"

    events: list[str] = []
    events_lock = threading.Lock()

    def record(event: str) -> None:
        with events_lock:
            events.append(event)

    worker_a_acquired = threading.Event()
    release_worker_a = threading.Event()
    worker_b_acquired = threading.Event()

    def worker_a() -> None:
        acquired = store.try_acquire(
            tenant_id=tenant_id,
            customer_key=customer_key,
            holder_id="worker-a",
            lease_duration=timedelta(seconds=30),
        )
        assert acquired
        record("a_acquired")
        worker_a_acquired.set()

        # Simulated advance()-shaped critical section / parser delay: the
        # acquire above has already committed and closed, so nothing here
        # holds a DB connection or transaction open.
        release_worker_a.wait(timeout=10)

        store.release(tenant_id=tenant_id, customer_key=customer_key, holder_id="worker-a")
        record("a_released")

    def worker_b() -> None:
        worker_a_acquired.wait(timeout=10)

        while True:
            acquired = store.try_acquire(
                tenant_id=tenant_id,
                customer_key=customer_key,
                holder_id="worker-b",
                lease_duration=timedelta(seconds=30),
            )
            if acquired:
                record("b_acquired")
                worker_b_acquired.set()
                return

            record("b_blocked")
            # Worker A still holds a live lease (RETURNING matched no rows).
            # Tell A to release, then retry.
            release_worker_a.set()
            time.sleep(0.05)

    try:
        thread_a = threading.Thread(target=worker_a)
        thread_b = threading.Thread(target=worker_b)
        thread_a.start()
        thread_b.start()
        thread_a.join(timeout=10)
        thread_b.join(timeout=10)

        assert worker_b_acquired.is_set()
        assert "a_acquired" in events
        assert "b_blocked" in events
        assert "a_released" in events
        assert "b_acquired" in events

        # Order proof: A acquires; B is blocked at least once while A holds
        # the lease; only after A releases does B's acquire succeed.
        assert events.index("a_acquired") < events.index("b_blocked")
        assert events.index("b_blocked") < events.index("a_released")
        assert events.index("a_released") < events.index("b_acquired")

        store.release(tenant_id=tenant_id, customer_key=customer_key, holder_id="worker-b")
    finally:
        _cleanup_tenant(engine, tenant_id)
        engine.dispose()


@pytest.mark.live_postgres
def test_different_customers_do_not_block_each_other() -> None:
    store, engine, tenant_id = _live_store()
    customer_key_a = f"whatsapp:+57{uuid4().hex[:10]}"
    customer_key_b = f"whatsapp:+57{uuid4().hex[:10]}"

    worker_a_acquired = threading.Event()
    worker_b_done = threading.Event()
    results: dict[str, bool] = {}

    def worker_a() -> None:
        acquired = store.try_acquire(
            tenant_id=tenant_id,
            customer_key=customer_key_a,
            holder_id="worker-a",
            lease_duration=timedelta(seconds=30),
        )
        results["a"] = acquired
        worker_a_acquired.set()

        # Hold customer A's claim for the rest of the test - if worker B had
        # to wait for this, it would time out.
        worker_b_done.wait(timeout=10)

    def worker_b() -> None:
        worker_a_acquired.wait(timeout=10)

        acquired = store.try_acquire(
            tenant_id=tenant_id,
            customer_key=customer_key_b,
            holder_id="worker-b",
            lease_duration=timedelta(seconds=30),
        )
        results["b"] = acquired
        worker_b_done.set()

    try:
        thread_a = threading.Thread(target=worker_a)
        thread_b = threading.Thread(target=worker_b)
        thread_a.start()
        thread_b.start()
        thread_a.join(timeout=10)
        thread_b.join(timeout=10)

        assert results["a"] is True
        assert results["b"] is True

        row_a = _read_claim(engine, tenant_id=tenant_id, customer_key=customer_key_a)
        row_b = _read_claim(engine, tenant_id=tenant_id, customer_key=customer_key_b)
        assert row_a is not None and row_a.holder_id == "worker-a"
        assert row_b is not None and row_b.holder_id == "worker-b"

        store.release(tenant_id=tenant_id, customer_key=customer_key_a, holder_id="worker-a")
        store.release(tenant_id=tenant_id, customer_key=customer_key_b, holder_id="worker-b")
    finally:
        _cleanup_tenant(engine, tenant_id)
        engine.dispose()


@pytest.mark.live_postgres
def test_acquire_and_release_hold_no_connection_during_simulated_parser_delay() -> None:
    store, engine, tenant_id = _live_store()
    customer_key = f"whatsapp:+57{uuid4().hex[:10]}"

    try:
        acquired = store.try_acquire(
            tenant_id=tenant_id,
            customer_key=customer_key,
            holder_id="solo-holder",
            lease_duration=timedelta(seconds=30),
        )
        assert acquired is True

        # The store's transaction has already committed and closed - no
        # connection should be checked out of the pool here.
        assert engine.pool.checkedout() == 0

        # Simulated parser/LLM delay: no DB call, no transaction, no
        # connection.
        time.sleep(0.1)
        assert engine.pool.checkedout() == 0

        released = store.release(
            tenant_id=tenant_id,
            customer_key=customer_key,
            holder_id="solo-holder",
        )
        assert released is True
        assert engine.pool.checkedout() == 0
    finally:
        _cleanup_tenant(engine, tenant_id)
        engine.dispose()
