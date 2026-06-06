from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from duna_orders.storage import factory
from duna_orders.storage.memory import InMemoryStorage
from duna_orders.storage.postgres import PostgresStorage


def _settings(
    *,
    backend: str,
    dashboard_target: str = "runtime",
    runtime_sheet_id: str | None = "runtime-sheet",
    demo_sheet_id: str | None = "demo-sheet",
    credentials_path: str | Path | None = "credentials/test-service-account.json",
    database_url: str | None = "sqlite:///storage-factory-test.db",
):
    is_demo = dashboard_target == "demo"

    return SimpleNamespace(
        duna_storage_backend=backend,
        dashboard_target=dashboard_target,
        google_sheets_spreadsheet_id=runtime_sheet_id,
        google_sheets_demo_spreadsheet_id=demo_sheet_id,
        dashboard_spreadsheet_id=demo_sheet_id if is_demo else runtime_sheet_id,
        is_dashboard_demo_target=is_demo,
        google_sheets_credentials_path=credentials_path,
        database_url=database_url,
    )


def test_build_storage_returns_inmemory_for_memory_backend() -> None:
    storage = factory.build_storage(_settings(backend="memory"))

    assert isinstance(storage, InMemoryStorage)


def test_build_storage_returns_inmemory_for_blank_backend() -> None:
    storage = factory.build_storage(_settings(backend=" "))

    assert isinstance(storage, InMemoryStorage)


def test_build_storage_builds_sheets_storage_for_runtime_target(monkeypatch) -> None:
    calls = {}

    class FakeGoogleSheetsStorage:
        def __init__(self, *, spreadsheet_id: str, credentials_path: str) -> None:
            calls["spreadsheet_id"] = spreadsheet_id
            calls["credentials_path"] = credentials_path

    monkeypatch.setattr(factory, "GoogleSheetsStorage", FakeGoogleSheetsStorage)

    storage = factory.build_storage(
        _settings(
            backend="sheets",
            dashboard_target="runtime",
            runtime_sheet_id="runtime-sheet-123",
        )
    )

    assert isinstance(storage, FakeGoogleSheetsStorage)
    assert calls == {
        "spreadsheet_id": "runtime-sheet-123",
        "credentials_path": "credentials/test-service-account.json",
    }


def test_build_storage_builds_sheets_storage_for_demo_target(monkeypatch) -> None:
    calls = {}

    class FakeGoogleSheetsStorage:
        def __init__(self, *, spreadsheet_id: str, credentials_path: str) -> None:
            calls["spreadsheet_id"] = spreadsheet_id
            calls["credentials_path"] = credentials_path

    monkeypatch.setattr(factory, "GoogleSheetsStorage", FakeGoogleSheetsStorage)

    storage = factory.build_storage(
        _settings(
            backend="sheets",
            dashboard_target="demo",
            runtime_sheet_id="runtime-sheet-123",
            demo_sheet_id="demo-sheet-456",
        )
    )

    assert isinstance(storage, FakeGoogleSheetsStorage)
    assert calls == {
        "spreadsheet_id": "demo-sheet-456",
        "credentials_path": "credentials/test-service-account.json",
    }


def test_build_storage_raises_for_sheets_backend_without_runtime_sheet_id() -> None:
    with pytest.raises(RuntimeError, match="GOOGLE_SHEETS_SPREADSHEET_ID"):
        factory.build_storage(
            _settings(
                backend="sheets",
                dashboard_target="runtime",
                runtime_sheet_id=None,
            )
        )


def test_build_storage_raises_for_sheets_backend_without_demo_sheet_id() -> None:
    with pytest.raises(RuntimeError, match="GOOGLE_SHEETS_DEMO_SPREADSHEET_ID"):
        factory.build_storage(
            _settings(
                backend="sheets",
                dashboard_target="demo",
                demo_sheet_id=None,
            )
        )


def test_build_storage_builds_postgres_storage_offline(monkeypatch) -> None:
    calls = {}

    def fake_session_factory():
        raise AssertionError("Postgres construction must not connect")

    def fake_get_or_create_session_factory(database_url: str):
        calls["database_url"] = database_url
        return fake_session_factory

    monkeypatch.setattr(
        factory,
        "get_or_create_session_factory",
        fake_get_or_create_session_factory,
    )

    storage = factory.build_storage(
        _settings(
            backend="postgres",
            database_url="sqlite:///offline-test.db",
        )
    )

    assert isinstance(storage, PostgresStorage)
    assert calls == {
        "database_url": "sqlite:///offline-test.db",
    }

def test_build_storage_raises_for_postgres_without_database_url() -> None:
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        factory.build_storage(_settings(backend="postgres", database_url=None))


def test_build_storage_raises_for_invalid_backend() -> None:
    with pytest.raises(RuntimeError, match="DUNA_STORAGE_BACKEND must be"):
        factory.build_storage(_settings(backend="sqlite"))