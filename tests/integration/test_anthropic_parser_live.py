from decimal import Decimal

import pytest

from duna_orders.config import settings
from duna_orders.domain.models import ParseResult, Product
from duna_orders.parsing.anthropic_parser import AnthropicParser


pytestmark = pytest.mark.live_api


def test_anthropic_parser_live_returns_parse_result():
    if not settings.anthropic_api_key:
        pytest.skip("ANTHROPIC_API_KEY is not set")

    parser = AnthropicParser()

    products = [
    Product(
        tenant_id="el-fogon-colombiano",
        product_id="prd_pollo",
        product_name="Pollo entero",
        aliases=["pollo", "pollo entero"],
        unit="unidad",
        unit_price=Decimal("25000"),
        current_stock=Decimal("10"),
    ),
    Product(
        tenant_id="el-fogon-colombiano",
        product_id="prd_gaseosa",
        product_name="Gaseosa 1.5L",
        aliases=["gaseosa", "gaseosa grande"],
        unit="unidad",
        unit_price=Decimal("6500"),
        current_stock=Decimal("30"),
    ),
]

    result = parser.parse(
        "Buenas, regálame 2 pollos enteros y 3 gaseosas grandes para mañana",
        products,
    )
    assert result.request.tenant_id == "el-fogon-colombiano"
    assert all(
        item.tenant_id == "el-fogon-colombiano"
        for item in result.request.items
    )
    assert isinstance(result, ParseResult)
    assert result.request.raw_message
    assert result.model == parser.model_name
    assert result.latency_ms >= 0
    assert result.raw_response