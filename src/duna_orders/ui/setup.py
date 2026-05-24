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


def get_storage() -> StorageInterface:
    # Future backend switch can check GOOGLE_SHEETS_SPREADSHEET_ID here.
    return InMemoryStorage()


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

def seed_inmemory_from_catalog(
    storage: InMemoryStorage,
    catalog: DemoCatalogFile,
) -> None:
    for product in catalog.products:
        storage.upsert_product(product)