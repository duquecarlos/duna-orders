from __future__ import annotations

from pathlib import Path

import pytest

from duna_orders.storage import factory
from duna_orders.storage.postgres import PostgresStorage
from duna_orders.storage.postgres_session import (
    get_or_create_engine,
    get_or_create_session_factory,
    reset_engine_cache,
)


@pytest.fixture(autouse=True)
def _reset_engine_cache() -> None:
    reset_engine_cache()
    yield
    reset_engine_cache()


def _sqlite_url(path: Path) -> str:
    return f"sqlite+pysqlite:///{path}"


def test_same_url_reuses_same_engine_object(tmp_path: Path) -> None:
    database_url = _sqlite_url(tmp_path / "same-url.db")

    first = get_or_create_engine(database_url)
    second = get_or_create_engine(database_url)

    assert first is second


def test_different_urls_create_different_engines(tmp_path: Path) -> None:
    first = get_or_create_engine(_sqlite_url(tmp_path / "first.db"))
    second = get_or_create_engine(_sqlite_url(tmp_path / "second.db"))

    assert first is not second


def test_session_factory_reuses_cached_engine_for_same_url(tmp_path: Path) -> None:
    database_url = _sqlite_url(tmp_path / "session-factory.db")

    engine = get_or_create_engine(database_url)
    session_factory = get_or_create_session_factory(database_url)

    assert session_factory.kw["bind"] is engine


def test_reset_engine_cache_clears_cache_and_disposes_old_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disposed = []

    class FakeEngine:
        def dispose(self) -> None:
            disposed.append(self)

    created_engines = []

    def fake_make_engine(database_url: str, *, echo: bool = False) -> FakeEngine:
        del database_url
        del echo
        engine = FakeEngine()
        created_engines.append(engine)
        return engine

    monkeypatch.setattr(
        "duna_orders.storage.postgres_session.make_engine",
        fake_make_engine,
    )

    first = get_or_create_engine("sqlite+pysqlite:///reset-test.db")

    reset_engine_cache()

    second = get_or_create_engine("sqlite+pysqlite:///reset-test.db")

    assert first is not second
    assert created_engines == [first, second]
    assert disposed == [first]


def test_engine_and_session_factory_creation_are_lazy(tmp_path: Path) -> None:
    database_path = tmp_path / "lazy.db"
    database_url = _sqlite_url(database_path)

    get_or_create_engine(database_url)
    get_or_create_session_factory(database_url)

    assert not database_path.exists()


def test_factory_reuses_cached_engine_across_postgres_storage_instances(
    tmp_path: Path,
) -> None:
    database_url = _sqlite_url(tmp_path / "factory-cache.db")
    settings = factory_settings(database_url=database_url)

    first = factory.build_storage(settings)
    second = factory.build_storage(settings)

    assert isinstance(first, PostgresStorage)
    assert isinstance(second, PostgresStorage)
    assert first._session_factory.kw["bind"] is second._session_factory.kw["bind"]


def test_factory_postgres_storage_construction_is_lazy(tmp_path: Path) -> None:
    database_path = tmp_path / "factory-lazy.db"
    settings = factory_settings(database_url=_sqlite_url(database_path))

    storage = factory.build_storage(settings)

    assert isinstance(storage, PostgresStorage)
    assert not database_path.exists()


def factory_settings(*, database_url: str):
    return type(
        "Settings",
        (),
        {
            "duna_storage_backend": "postgres",
            "database_url": database_url,
            "dashboard_spreadsheet_id": None,
            "is_dashboard_demo_target": False,
            "google_sheets_credentials_path": None,
        },
    )()