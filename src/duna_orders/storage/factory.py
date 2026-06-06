from __future__ import annotations

from duna_orders.config import Settings
from duna_orders.storage.base import StorageInterface
from duna_orders.storage.memory import InMemoryStorage
from duna_orders.storage.postgres import PostgresStorage
from duna_orders.storage.postgres_session import make_engine, make_session_factory
from duna_orders.storage.sheets import GoogleSheetsStorage


def build_storage(settings: Settings) -> StorageInterface:
    backend = settings.duna_storage_backend.strip().lower()

    if backend in {"", "memory"}:
        return InMemoryStorage()

    if backend == "sheets":
        return _build_sheets_storage(settings)

    if backend == "postgres":
        return _build_postgres_storage(settings)

    raise RuntimeError(
        "DUNA_STORAGE_BACKEND must be 'memory', 'sheets', or 'postgres'. "
        f"Received: {settings.duna_storage_backend!r}"
    )


def _build_sheets_storage(settings: Settings) -> GoogleSheetsStorage:
    spreadsheet_id = settings.dashboard_spreadsheet_id

    if settings.is_dashboard_demo_target and not spreadsheet_id:
        raise RuntimeError(
            "DASHBOARD_TARGET=demo requires GOOGLE_SHEETS_DEMO_SPREADSHEET_ID."
        )

    if not spreadsheet_id:
        raise RuntimeError(
            "DUNA_STORAGE_BACKEND=sheets requires GOOGLE_SHEETS_SPREADSHEET_ID."
        )

    if not settings.google_sheets_credentials_path:
        raise RuntimeError(
            "DUNA_STORAGE_BACKEND=sheets requires GOOGLE_SHEETS_CREDENTIALS_PATH."
        )

    return GoogleSheetsStorage(
        spreadsheet_id=spreadsheet_id,
        credentials_path=str(settings.google_sheets_credentials_path),
    )


def _build_postgres_storage(settings: Settings) -> PostgresStorage:
    if not settings.database_url:
        raise RuntimeError("DUNA_STORAGE_BACKEND=postgres requires DATABASE_URL to be set.")

    engine = make_engine(settings.database_url)
    return PostgresStorage(make_session_factory(engine))