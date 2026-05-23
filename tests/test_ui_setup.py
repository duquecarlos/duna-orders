from __future__ import annotations

from duna_orders.domain.models import ParseResult, Product
from duna_orders.parsing.base import ParserInterface
from duna_orders.services.orders import OrderService
from duna_orders.services.parsing import ParsingService
from duna_orders.storage.memory import InMemoryStorage
from duna_orders.ui import setup


class FakeParser(ParserInterface):
    @property
    def model_name(self) -> str:
        return "fake-parser"

    def parse(self, raw_message: str, products: list[Product]) -> ParseResult:
        raise NotImplementedError


def test_get_storage_returns_inmemory_storage() -> None:
    storage = setup.get_storage()

    assert isinstance(storage, InMemoryStorage)


def test_get_order_service_returns_service_bound_to_storage() -> None:
    storage = InMemoryStorage()

    service = setup.get_order_service(storage)

    assert isinstance(service, OrderService)
    assert service._storage is storage


def test_get_parsing_service_returns_none_without_api_key(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    service = setup.get_parsing_service(InMemoryStorage())

    assert service is None


def test_get_parsing_service_returns_service_when_api_key_is_set(monkeypatch) -> None:
    storage = InMemoryStorage()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(setup, "_build_anthropic_parser", lambda: FakeParser())

    service = setup.get_parsing_service(storage)

    assert isinstance(service, ParsingService)
    assert service._storage is storage


def test_get_demo_catalog_returns_validated_catalog() -> None:
    catalog = setup.get_demo_catalog()

    assert catalog.business.tenant_id == "el-fogon-colombiano"
    assert len(catalog.products) == 52


def test_seed_inmemory_from_catalog_seeds_all_products() -> None:
    storage = InMemoryStorage()
    catalog = setup.get_demo_catalog()

    setup.seed_inmemory_from_catalog(storage, catalog)

    assert len(storage.list_products(active_only=False)) == 52