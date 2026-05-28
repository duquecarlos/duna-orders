from __future__ import annotations

from duna_orders.config import settings

import streamlit as st

from duna_orders.demo_catalog import DemoCatalogFile, load_demo_catalog
from duna_orders.demo_messages import DemoMessagesFile, load_demo_messages
from duna_orders.parsing.base import ParserInterface
from duna_orders.services.orders import OrderService
from duna_orders.services.parsing import ParsingService
from duna_orders.storage.base import StorageInterface
from duna_orders.storage.memory import InMemoryStorage
from duna_orders.storage.sheets import GoogleSheetsStorage


def get_storage() -> StorageInterface:
    backend = settings.duna_storage_backend.strip().lower()

    if backend in {"", "memory"}:
        return InMemoryStorage()

    if backend == "sheets":
        spreadsheet_id = settings.dashboard_spreadsheet_id

        if settings.is_dashboard_demo_target and not spreadsheet_id:
            raise RuntimeError(
                "DASHBOARD_TARGET=demo requires "
                "GOOGLE_SHEETS_DEMO_SPREADSHEET_ID."
            )

        if not spreadsheet_id:
            raise RuntimeError(
                "DUNA_STORAGE_BACKEND=sheets requires "
                "GOOGLE_SHEETS_SPREADSHEET_ID."
            )

        if not settings.google_sheets_credentials_path:
            raise RuntimeError(
                "DUNA_STORAGE_BACKEND=sheets requires GOOGLE_SHEETS_CREDENTIALS_PATH."
            )

        return GoogleSheetsStorage(
            spreadsheet_id=spreadsheet_id,
            credentials_path=str(settings.google_sheets_credentials_path),
        )

    raise RuntimeError(
        "DUNA_STORAGE_BACKEND must be 'memory' or 'sheets'. "
        f"Received: {settings.duna_storage_backend!r}"
    )


def get_order_service(storage: StorageInterface) -> OrderService:
    return OrderService(storage)


def _build_anthropic_parser() -> ParserInterface:
    from duna_orders.parsing.anthropic_parser import AnthropicParser

    return AnthropicParser()


def get_parsing_service(storage: StorageInterface) -> ParsingService | None:
    if not settings.anthropic_api_key:
        return None

    return ParsingService(
        parser=_build_anthropic_parser(),
        storage=storage,
    )


@st.cache_data
def get_demo_catalog() -> DemoCatalogFile:
    return load_demo_catalog()


@st.cache_data
def get_demo_messages() -> DemoMessagesFile:
    return load_demo_messages()

def prepare_storage_catalog(
    storage: StorageInterface,
    catalog: DemoCatalogFile,
) -> bool:
    if isinstance(storage, InMemoryStorage):
        seed_inmemory_from_catalog(storage, catalog)
        return True

    if isinstance(storage, GoogleSheetsStorage):
        return bool(storage.list_products(active_only=False))

    return True

def seed_inmemory_from_catalog(
    storage: InMemoryStorage,
    catalog: DemoCatalogFile,
) -> None:
    for product in catalog.products:
        storage.upsert_product(product)