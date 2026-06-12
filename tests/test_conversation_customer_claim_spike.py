"""M9.6B validation spike for the durable per-customer claim/lock row.

This module validates the primitive recommended in
`docs/M9_6_CONVERSATION_UOW_DESIGN.md` section 6/7 (a durable
`(tenant_id, customer_key)`-unique claim row with lease semantics) against
real Postgres behavior.

This is a spike, not production code:

* The `conversation_customer_claims_spike` table is created and dropped by
  this module's fixture - it is not an Alembic-managed table and is never
  part of `Base.metadata`.
* `acquire_claim` / `release_claim` are test-local helper functions, not a
  production store class. They are not wired into
  `ConversationAdvancementService.advance(...)` or any other runtime code.
* Each helper performs exactly one short `engine.begin()` transaction. The
  simulated "parser delay" in the tests below runs with no DB connection or
  transaction open, matching the M9.6A design's requirement that the claim
  survive across the parser/LLM call as committed row state, not a held
  connection/transaction.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from duna_orders.config import settings
from duna_orders.storage.postgres_session import make_engine


pytestmark = pytest.mark.live_postgres


CLAIMS_TABLE = "conversation_customer_claims_spike"


def _require_database_url() -> str:
    if not settings.database_url:
        pytest.skip("DATABASE_URL is required for live_postgres tests")

    return settings.database_url


@pytest.fixture(scope="module")
def claims_engine():
    engine = make_engine(_require_database_url())

    with engine.begin() as connection:
        connection.execute(text(f"DROP TABLE IF EXISTS {CLAIMS_TABLE}"))
        connection.execute(
            text(
                f"""
                CREATE TABLE {CLAIMS_TABLE} (
                    tenant_id TEXT NOT NULL,
                    customer_key TEXT NOT NULL,
                    holder_id TEXT NOT NULL,
                    acquired_at TIMESTAMPTZ NOT NULL,
                    lease_expires_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (tenant_id, customer_key)
                )
                """
            )
        )

    try:
        yield engine
    finally:
        with engine.begin() as connection:
            connection.execute(text(f"DROP TABLE IF EXISTS {CLAIMS_TABLE}"))
        engine.dispose()


# --- spike primitive: each call is exactly one short transaction ---


def acquire_claim(
    engine: Engine,
    *,
    tenant_id: str,
    customer_key: str,
    holder_id: str,
    now: datetime,
    lease_duration: timedelta,
) -> bool:
    """Acquire the claim for (tenant_id, customer_key), or reclaim it if the
    existing holder's lease has already expired.

    Atomic via INSERT ... ON CONFLICT ... DO UPDATE ... WHERE <expired>
    RETURNING: if no row exists, it is inserted; if a row exists with an
    expired lease, it is overwritten with the new holder; if a row exists
    with a live (non-expired) lease, the UPDATE's WHERE clause matches no
    rows and RETURNING yields nothing.

    One short transaction; no connection is held after this returns.
    """
    lease_expires_at = now + lease_duration

    with engine.begin() as connection:
        row = connection.execute(
            text(
                f"""
                INSERT INTO {CLAIMS_TABLE}
                    (tenant_id, customer_key, holder_id, acquired_at, lease_expires_at, updated_at)
                VALUES
                    (:tenant_id, :customer_key, :holder_id, :now, :lease_expires_at, :now)
                ON CONFLICT (tenant_id, customer_key) DO UPDATE SET
                    holder_id = EXCLUDED.holder_id,
                    acquired_at = EXCLUDED.acquired_at,
                    lease_expires_at = EXCLUDED.lease_expires_at,
                    updated_at = EXCLUDED.updated_at
                WHERE {CLAIMS_TABLE}.lease_expires_at <= :now
                RETURNING holder_id
                """
            ),
            {
                "tenant_id": tenant_id,
                "customer_key": customer_key,
                "holder_id": holder_id,
                "now": now,
                "lease_expires_at": lease_expires_at,
            },
        ).first()

    return row is not None


def release_claim(
    engine: Engine,
    *,
    tenant_id: str,
    customer_key: str,
    holder_id: str,
) -> None:
    """Release the claim, but only if still held by `holder_id`.

    One short transaction; no connection is held after this returns.
    """
    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                DELETE FROM {CLAIMS_TABLE}
                WHERE tenant_id = :tenant_id
                  AND customer_key = :customer_key
                  AND holder_id = :holder_id
                """
            ),
            {
                "tenant_id": tenant_id,
                "customer_key": customer_key,
                "holder_id": holder_id,
            },
        )


def _read_claim(engine: Engine, *, tenant_id: str, customer_key: str):
    with engine.connect() as connection:
        return connection.execute(
            text(
                f"""
                SELECT holder_id, lease_expires_at
                FROM {CLAIMS_TABLE}
                WHERE tenant_id = :tenant_id AND customer_key = :customer_key
                """
            ),
            {"tenant_id": tenant_id, "customer_key": customer_key},
        ).first()


# --- 1. same customer serializes ---


def test_same_customer_claim_serializes_concurrent_workers(claims_engine: Engine) -> None:
    engine = claims_engine
    tenant_id = f"tenant_spike_{uuid4().hex}"
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
        acquired = acquire_claim(
            engine,
            tenant_id=tenant_id,
            customer_key=customer_key,
            holder_id="worker-a",
            now=datetime.now(timezone.utc),
            lease_duration=timedelta(seconds=30),
        )
        assert acquired
        record("a_acquired")
        worker_a_acquired.set()

        # Simulated advance()-shaped critical section / parser delay: the
        # acquire transaction above has already committed and closed, so
        # nothing here holds a DB connection or transaction open.
        release_worker_a.wait(timeout=10)

        release_claim(engine, tenant_id=tenant_id, customer_key=customer_key, holder_id="worker-a")
        record("a_released")

    def worker_b() -> None:
        worker_a_acquired.wait(timeout=10)

        while True:
            acquired = acquire_claim(
                engine,
                tenant_id=tenant_id,
                customer_key=customer_key,
                holder_id="worker-b",
                now=datetime.now(timezone.utc),
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

    # Order proof: A acquires; B is blocked at least once while A holds the
    # lease; only after A releases does B's acquire succeed.
    assert events.index("a_acquired") < events.index("b_blocked")
    assert events.index("b_blocked") < events.index("a_released")
    assert events.index("a_released") < events.index("b_acquired")

    release_claim(engine, tenant_id=tenant_id, customer_key=customer_key, holder_id="worker-b")


# --- 2. different customers do not block each other ---


def test_different_customers_do_not_block_each_other(claims_engine: Engine) -> None:
    engine = claims_engine
    tenant_id = f"tenant_spike_{uuid4().hex}"
    customer_key_a = f"whatsapp:+57{uuid4().hex[:10]}"
    customer_key_b = f"whatsapp:+57{uuid4().hex[:10]}"

    worker_a_acquired = threading.Event()
    worker_b_done = threading.Event()
    results: dict[str, bool] = {}

    def worker_a() -> None:
        acquired = acquire_claim(
            engine,
            tenant_id=tenant_id,
            customer_key=customer_key_a,
            holder_id="worker-a",
            now=datetime.now(timezone.utc),
            lease_duration=timedelta(seconds=30),
        )
        results["a"] = acquired
        worker_a_acquired.set()

        # Hold customer A's claim for the rest of the test (no release
        # here) - if worker B had to wait for this, it would time out.
        worker_b_done.wait(timeout=10)

    def worker_b() -> None:
        worker_a_acquired.wait(timeout=10)

        acquired = acquire_claim(
            engine,
            tenant_id=tenant_id,
            customer_key=customer_key_b,
            holder_id="worker-b",
            now=datetime.now(timezone.utc),
            lease_duration=timedelta(seconds=30),
        )
        results["b"] = acquired
        worker_b_done.set()

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

    release_claim(engine, tenant_id=tenant_id, customer_key=customer_key_a, holder_id="worker-a")
    release_claim(engine, tenant_id=tenant_id, customer_key=customer_key_b, holder_id="worker-b")


# --- 3. lease / recovery ---


def test_expired_lease_can_be_taken_over_but_live_lease_cannot(claims_engine: Engine) -> None:
    engine = claims_engine
    tenant_id = f"tenant_spike_{uuid4().hex}"
    customer_key = f"whatsapp:+57{uuid4().hex[:10]}"
    now = datetime.now(timezone.utc)

    # Seed a stale claim: acquired in the past with a lease that has already
    # expired (simulates a crashed holder).
    seeded = acquire_claim(
        engine,
        tenant_id=tenant_id,
        customer_key=customer_key,
        holder_id="stale-holder",
        now=now - timedelta(minutes=10),
        lease_duration=timedelta(minutes=5),
    )
    assert seeded is True

    row = _read_claim(engine, tenant_id=tenant_id, customer_key=customer_key)
    assert row is not None
    assert row.holder_id == "stale-holder"
    assert row.lease_expires_at < now

    # A new holder can take over an expired claim.
    took_over = acquire_claim(
        engine,
        tenant_id=tenant_id,
        customer_key=customer_key,
        holder_id="new-holder",
        now=now,
        lease_duration=timedelta(seconds=30),
    )
    assert took_over is True

    row = _read_claim(engine, tenant_id=tenant_id, customer_key=customer_key)
    assert row is not None
    assert row.holder_id == "new-holder"
    assert row.lease_expires_at > now

    # A live (non-expired) claim cannot be taken over by another holder.
    blocked = acquire_claim(
        engine,
        tenant_id=tenant_id,
        customer_key=customer_key,
        holder_id="another-holder",
        now=now,
        lease_duration=timedelta(seconds=30),
    )
    assert blocked is False

    row = _read_claim(engine, tenant_id=tenant_id, customer_key=customer_key)
    assert row is not None
    assert row.holder_id == "new-holder"  # unchanged

    release_claim(engine, tenant_id=tenant_id, customer_key=customer_key, holder_id="new-holder")


# --- 4. short transactions / parser delay outside any open transaction ---


def test_acquire_and_release_hold_no_connection_during_simulated_parser_delay(
    claims_engine: Engine,
) -> None:
    engine = claims_engine
    tenant_id = f"tenant_spike_{uuid4().hex}"
    customer_key = f"whatsapp:+57{uuid4().hex[:10]}"
    now = datetime.now(timezone.utc)

    acquired = acquire_claim(
        engine,
        tenant_id=tenant_id,
        customer_key=customer_key,
        holder_id="solo-holder",
        now=now,
        lease_duration=timedelta(seconds=30),
    )
    assert acquired is True

    # acquire_claim's `with engine.begin()` block has already committed and
    # closed - no connection should be checked out of the pool here.
    assert engine.pool.checkedout() == 0

    # Simulated parser/LLM delay: no DB call, no transaction, no connection.
    time.sleep(0.1)
    assert engine.pool.checkedout() == 0

    release_claim(engine, tenant_id=tenant_id, customer_key=customer_key, holder_id="solo-holder")
    assert engine.pool.checkedout() == 0
