from typing import Literal

from ulid import ULID


EntityPrefix = Literal["ord", "cus", "prd", "oit", "mov", "prs"]


def new_id(prefix: EntityPrefix) -> str:
    return f"{prefix}_{str(ULID()).lower()}"


def new_order_id() -> str:
    return new_id("ord")


def new_customer_id() -> str:
    return new_id("cus")


def new_product_id() -> str:
    return new_id("prd")


def new_order_item_id() -> str:
    return new_id("oit")


def new_stock_movement_id() -> str:
    return new_id("mov")


def new_parse_session_id() -> str:
    return new_id("prs")