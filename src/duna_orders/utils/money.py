from __future__ import annotations

from decimal import Decimal


def format_cop(value: Decimal) -> str:
    return f"${value:,.0f}".replace(",", ".")
