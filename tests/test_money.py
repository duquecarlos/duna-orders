from decimal import Decimal

from duna_orders.utils.money import format_cop


def test_format_cop_uses_colombian_thousands_separator() -> None:
    assert format_cop(Decimal("85000")) == "$85.000"


def test_format_cop_rounds_to_whole_pesos() -> None:
    assert format_cop(Decimal("1234.56")) == "$1.235"
