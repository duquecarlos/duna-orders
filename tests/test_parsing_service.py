from decimal import Decimal

import pytest

from duna_orders.domain.models import (
    DraftItemRequest,
    DraftOrderRequest,
    ParseResult,
    Product,
)
from duna_orders.ids import new_id
from duna_orders.parsing.exceptions import ParserAPIError
from duna_orders.services.parsing import ParsingService
from duna_orders.storage.memory import InMemoryStorage
from tests._fakes import MockParser
from tests.conftest import DEFAULT_TEST_TENANT_ID

def _seed_product(storage: InMemoryStorage) -> Product:
    product = Product(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        product_id=new_id("prd"),
        product_name="Pollo entero",
        unit_price=Decimal("25000"),
        current_stock=Decimal("10"),
    )
    storage.upsert_product(product)
    return product


def test_parsing_service_success_writes_parse_log():
    storage = InMemoryStorage()
    product = _seed_product(storage)
    canned = ParseResult(
        request=DraftOrderRequest(
            tenant_id=DEFAULT_TEST_TENANT_ID,
            raw_message="me regala 2 pollos",
            customer_name="",
            items=[
                DraftItemRequest(
                    tenant_id=DEFAULT_TEST_TENANT_ID,
                    product_id=product.product_id,
                    quantity=Decimal("2"),
                )
            ],
        ),
        warnings=[],
        model="mock-parser",
        latency_ms=42,
        raw_response='{"items":[{"product_id":"prd_X","quantity":2}]}',
    )
    parser = MockParser(result=canned)
    service = ParsingService(parser, storage)

    result = service.parse(tenant_id=DEFAULT_TEST_TENANT_ID,
                           raw_message="me regala 2 pollos", 
                           products=storage.list_products())

    assert result == canned
    assert len(storage._parse_logs) == 1

    entry = storage._parse_logs[0]

    assert entry.tenant_id == DEFAULT_TEST_TENANT_ID
    assert entry.success is True
    assert entry.error is None
    assert entry.model == "mock-parser"
    assert entry.latency_ms == 42
    assert "product_id" in entry.parsed_json


def test_parsing_service_failure_writes_parse_log_and_reraises():
    storage = InMemoryStorage()
    _seed_product(storage)
    parser = MockParser(raise_error=ParserAPIError("network down"))
    service = ParsingService(parser, storage)

    with pytest.raises(ParserAPIError):
        service.parse(tenant_id=DEFAULT_TEST_TENANT_ID,
                      raw_message="hola",
                      products=storage.list_products())

    assert len(storage._parse_logs) == 1

    entry = storage._parse_logs[0]

    assert entry.tenant_id == DEFAULT_TEST_TENANT_ID
    assert entry.success is False
    assert entry.error is not None
    assert "network down" in entry.error
    assert entry.model == "mock-parser"
    assert entry.parsed_json == ""


def test_parsing_service_does_not_modify_storage_beyond_parse_log():
    storage = InMemoryStorage()
    _seed_product(storage)
    parser = MockParser()
    service = ParsingService(parser, storage)

    products_before = storage.list_products()
    orders_before = storage.list_orders()
    movements_before = storage.list_stock_movements()

    service.parse(tenant_id=DEFAULT_TEST_TENANT_ID,
                  raw_message="test message",
                  products=storage.list_products())

    assert storage.list_products() == products_before
    assert storage.list_orders() == orders_before
    assert storage.list_stock_movements() == movements_before
    assert len(storage._parse_logs) == 1


def test_mock_parser_records_calls():
    storage = InMemoryStorage()
    _seed_product(storage)
    parser = MockParser()
    service = ParsingService(parser, storage)

    products = storage.list_products()

    service.parse(
                tenant_id=DEFAULT_TEST_TENANT_ID,
                raw_message="first message",
                products=products,
            )
    service.parse(tenant_id=DEFAULT_TEST_TENANT_ID,
                  raw_message="second message",
                  products=products)

    assert len(parser.calls) == 2
    assert parser.calls[0][0] == "first message"
    assert parser.calls[1][0] == "second message"


def test_parsing_service_passes_products_unchanged_to_parser():
    storage = InMemoryStorage()
    _seed_product(storage)
    parser = MockParser()
    service = ParsingService(parser, storage)

    products = storage.list_products()

    service.parse(tenant_id=DEFAULT_TEST_TENANT_ID,
                  raw_message="anything",
                  products=products)

    received_products = parser.calls[0][1]

    assert received_products == products