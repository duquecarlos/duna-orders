from __future__ import annotations


import pytest
from sqlalchemy import func, select
import time

from alembic.command import upgrade
from alembic.config import Config
from duna_orders.config import settings
from duna_orders.demo_dataset import generate_demo_dataset
from duna_orders.storage.postgres import PostgresStorage
from duna_orders.storage.postgres_base import Base
from duna_orders.storage.postgres_models import (
    CustomerRow,
    OrderItemRow,
    OrderRow,
    ProductRow,
)
from duna_orders.storage.postgres_session import make_engine, make_session_factory


@pytest.fixture
def postgres_storage(tmp_path) -> PostgresStorage:
    database_path = tmp_path / "postgres_reseed_test.db"
    engine = make_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)

    return PostgresStorage(make_session_factory(engine))


def _tenant_count(
    storage: PostgresStorage,
    row_type,
    tenant_id: str,
) -> int:
    with storage._session_factory() as session:
        count = session.scalar(
            select(func.count())
            .select_from(row_type)
            .where(row_type.tenant_id == tenant_id)
        )

    return int(count or 0)


def _queried_counts(storage: PostgresStorage, tenant_id: str) -> dict[str, int]:
    return {
        "products": _tenant_count(storage, ProductRow, tenant_id),
        "customers": _tenant_count(storage, CustomerRow, tenant_id),
        "orders": _tenant_count(storage, OrderRow, tenant_id),
        "order_items": _tenant_count(storage, OrderItemRow, tenant_id),
    }
def _require_database_url() -> str:
    if not settings.database_url:
        pytest.skip("DATABASE_URL is required for live_postgres tests")

    return settings.database_url

def _expected_demo_counts() -> dict[str, int]:
    return {
        "products": 52,
        "customers": 730,
        "orders": 1500,
        "order_items": 3889,
    }


def test_reseed_demo_dataset_returns_and_persists_locked_counts(
    postgres_storage: PostgresStorage,
) -> None:
    dataset = generate_demo_dataset()

    counts = postgres_storage.reseed_demo_dataset(dataset)

    assert {
        key: counts[key]
        for key in ("products", "customers", "orders", "order_items")
    } == _expected_demo_counts()
    assert _queried_counts(postgres_storage, dataset.tenant_id) == _expected_demo_counts()


def test_reseed_demo_dataset_is_idempotent(
    postgres_storage: PostgresStorage,
) -> None:
    dataset = generate_demo_dataset()

    first_counts = postgres_storage.reseed_demo_dataset(dataset)
    second_counts = postgres_storage.reseed_demo_dataset(dataset)

    assert {
        key: first_counts[key]
        for key in ("products", "customers", "orders", "order_items")
    } == _expected_demo_counts()
    assert {
        key: second_counts[key]
        for key in ("products", "customers", "orders", "order_items")
    } == _expected_demo_counts()
    assert _queried_counts(postgres_storage, dataset.tenant_id) == _expected_demo_counts()


def test_wipe_tenant_data_requires_tenant_id(
    postgres_storage: PostgresStorage,
) -> None:
    with pytest.raises(ValueError, match="tenant_id is required"):
        postgres_storage.wipe_tenant_data("")


def test_reseed_demo_dataset_rolls_back_on_bulk_failure(
    postgres_storage: PostgresStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = generate_demo_dataset()
    postgres_storage.reseed_demo_dataset(dataset)

    def fail_order_items(*args, **kwargs) -> int:
        raise RuntimeError("simulated order item bulk failure")

    monkeypatch.setattr(postgres_storage, "bulk_create_order_items", fail_order_items)

    with pytest.raises(RuntimeError, match="simulated order item bulk failure"):
        postgres_storage.reseed_demo_dataset(dataset)

    assert _queried_counts(postgres_storage, dataset.tenant_id) == _expected_demo_counts()

@pytest.mark.live_postgres
def test_live_postgres_reseed_demo_dataset_against_neon() -> None:
    database_url = _require_database_url()

    upgrade(Config("alembic.ini"), "head")

    engine = make_engine(database_url)
    storage = PostgresStorage(make_session_factory(engine))
    dataset = generate_demo_dataset()

    try:
        started_at = time.perf_counter()

        counts = storage.reseed_demo_dataset(dataset)

        elapsed_s = time.perf_counter() - started_at
        print(f"live_postgres reseed elapsed_s={elapsed_s:.2f}")
        print(f"live_postgres reseed counts={counts}")

        assert {
            key: counts[key]
            for key in ("products", "customers", "orders", "order_items")
        } == _expected_demo_counts()
        assert _queried_counts(storage, dataset.tenant_id) == _expected_demo_counts()
        assert elapsed_s < 60
    finally:
        engine.dispose()