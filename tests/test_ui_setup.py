from __future__ import annotations

from duna_orders.domain.models import ParseResult, Product
from duna_orders.parsing.base import ParserInterface
from duna_orders.services.orders import OrderService
from duna_orders.services.parsing import ParsingService
from duna_orders.storage.memory import InMemoryStorage
from duna_orders.ui import setup
from duna_orders.storage.sheets import GoogleSheetsStorage
import pytest


class FakeParser(ParserInterface):
    @property
    def model_name(self) -> str:
        return "fake-parser"

    def parse(self, raw_message: str, products: list[Product]) -> ParseResult:
        raise NotImplementedError

def test_get_storage_returns_inmemory_storage_by_default(monkeypatch) -> None:
    monkeypatch.setattr(setup.settings, "duna_storage_backend", "memory")

    storage = setup.get_storage()

    assert isinstance(storage, InMemoryStorage)


def test_get_storage_returns_inmemory_storage_when_backend_is_blank(monkeypatch) -> None:
    monkeypatch.setattr(setup.settings, "duna_storage_backend", " ")

    storage = setup.get_storage()

    assert isinstance(storage, InMemoryStorage)


def test_get_storage_raises_for_invalid_backend(monkeypatch) -> None:
    monkeypatch.setattr(setup.settings, "duna_storage_backend", "sqlite")

    with pytest.raises(RuntimeError, match="DUNA_STORAGE_BACKEND must be"):
        setup.get_storage()


def test_get_storage_raises_for_sheets_backend_without_spreadsheet_id(monkeypatch) -> None:
    monkeypatch.setattr(setup.settings, "duna_storage_backend", "sheets")
    monkeypatch.setattr(setup.settings, "google_sheets_spreadsheet_id", None)

    with pytest.raises(RuntimeError, match="GOOGLE_SHEETS_SPREADSHEET_ID"):
        setup.get_storage()

def test_get_storage_builds_google_sheets_storage_when_configured(monkeypatch) -> None:
    calls = {}

    class FakeGoogleSheetsStorage:
        def __init__(self, *, spreadsheet_id: str, credentials_path: str) -> None:
            calls["spreadsheet_id"] = spreadsheet_id
            calls["credentials_path"] = credentials_path

    monkeypatch.setattr(setup.settings, "duna_storage_backend", "sheets")
    monkeypatch.setattr(setup.settings, "google_sheets_spreadsheet_id", "sheet-123")
    monkeypatch.setattr(
        setup.settings,
        "google_sheets_credentials_path",
        "credentials/test-service-account.json",
    )
    monkeypatch.setattr(setup, "GoogleSheetsStorage", FakeGoogleSheetsStorage)

    storage = setup.get_storage()

    assert isinstance(storage, FakeGoogleSheetsStorage)
    assert calls == {
        "spreadsheet_id": "sheet-123",
        "credentials_path": "credentials/test-service-account.json",
    }


def test_get_order_service_returns_service_bound_to_storage() -> None:
    storage = InMemoryStorage()

    service = setup.get_order_service(storage)

    assert isinstance(service, OrderService)
    assert service._storage is storage

def test_get_parsing_service_returns_none_without_api_key(monkeypatch) -> None:
    monkeypatch.setattr(setup.settings, "anthropic_api_key", None)

    service = setup.get_parsing_service(InMemoryStorage())

    assert service is None


def test_get_parsing_service_returns_service_when_api_key_is_set(monkeypatch) -> None:
    storage = InMemoryStorage()
    monkeypatch.setattr(setup.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(setup, "_build_anthropic_parser", lambda: FakeParser())

    service = setup.get_parsing_service(storage)

    assert isinstance(service, ParsingService)
    assert service._storage is storage

def test_get_demo_catalog_returns_validated_catalog() -> None:
    catalog = setup.get_demo_catalog()

    assert catalog.business.tenant_id == "el-fogon-colombiano"
    assert len(catalog.products) == 52

def test_get_demo_messages_returns_validated_messages() -> None:
    demo_messages = setup.get_demo_messages()

    assert len(demo_messages.messages) == 16
    assert demo_messages.messages[0].id.startswith("msg_")

def test_seed_inmemory_from_catalog_seeds_all_products() -> None:
    storage = InMemoryStorage()
    catalog = setup.get_demo_catalog()

    setup.seed_inmemory_from_catalog(storage, catalog)

    assert len(storage.list_products(active_only=False)) == 52

def test_prepare_storage_catalog_seeds_inmemory_storage() -> None:
    storage = InMemoryStorage()
    catalog = setup.get_demo_catalog()

    ready = setup.prepare_storage_catalog(storage, catalog)

    assert ready is True
    assert len(storage.list_products(active_only=False)) == 52


def test_prepare_storage_catalog_returns_true_for_seeded_sheets_storage(monkeypatch) -> None:
    catalog = setup.get_demo_catalog()

    class FakeGoogleSheetsStorage:
        def __init__(self) -> None:
            self.upsert_calls = 0

        def list_products(self, *, active_only: bool = True):
            return [catalog.products[0]]

        def upsert_product(self, product):
            self.upsert_calls += 1

    storage = FakeGoogleSheetsStorage()
    monkeypatch.setattr(setup, "GoogleSheetsStorage", FakeGoogleSheetsStorage)

    ready = setup.prepare_storage_catalog(storage, catalog)

    assert ready is True
    assert storage.upsert_calls == 0


def test_prepare_storage_catalog_returns_false_for_unseeded_sheets_storage(monkeypatch) -> None:
    catalog = setup.get_demo_catalog()

    class FakeGoogleSheetsStorage:
        def __init__(self) -> None:
            self.upsert_calls = 0

        def list_products(self, *, active_only: bool = True):
            return []

        def upsert_product(self, product):
            self.upsert_calls += 1

    storage = FakeGoogleSheetsStorage()
    monkeypatch.setattr(setup, "GoogleSheetsStorage", FakeGoogleSheetsStorage)

    ready = setup.prepare_storage_catalog(storage, catalog)

    assert ready is False
    assert storage.upsert_calls == 0