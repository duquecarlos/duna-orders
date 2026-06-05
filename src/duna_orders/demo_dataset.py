from __future__ import annotations

from dataclasses import dataclass

from duna_orders.demo_catalog import load_demo_catalog
from duna_orders.demo_customers import DEMO_TENANT_ID, build_demo_customers
from duna_orders.demo_orders import DEFAULT_DEMO_ORDER_COUNT, build_demo_order_dataset
from duna_orders.domain.models import Customer, Order, OrderItem, Product


@dataclass(frozen=True)
class DemoDataset:
    tenant_id: str
    seed: int
    customers: list[Customer]
    products: list[Product]
    orders: list[Order]
    order_items: list[OrderItem]


def generate_demo_dataset(
    *,
    seed: int = 42,
    tenant_id: str = DEMO_TENANT_ID,
    order_count: int = DEFAULT_DEMO_ORDER_COUNT,
) -> DemoDataset:
    customers = build_demo_customers(
        seed=seed,
        tenant_id=tenant_id,
    )
    catalog = load_demo_catalog()
    order_dataset = build_demo_order_dataset(
        customers=customers,
        products=catalog.products,
        order_count=order_count,
        seed=seed,
        tenant_id=tenant_id,
    )

    return DemoDataset(
        tenant_id=tenant_id,
        seed=seed,
        customers=customers,
        products=catalog.products,
        orders=order_dataset.orders,
        order_items=order_dataset.order_items,
    )