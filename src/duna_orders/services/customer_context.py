from __future__ import annotations

from dataclasses import dataclass

from duna_orders.domain.models import Customer, Order
from duna_orders.domain.phone import normalize_customer_phone
from duna_orders.storage.base import StorageInterface


@dataclass(frozen=True)
class CustomerContext:
    customer: Customer | None
    previous_orders: list[Order]

    @property
    def previous_order_count(self) -> int:
        return len(self.previous_orders)

    @property
    def is_known_customer(self) -> bool:
        return self.customer is not None


def get_customer_context_by_phone(
    storage: StorageInterface,
    *,
    tenant_id: str,
    phone: str | None,
    limit: int = 10,
) -> CustomerContext:
    normalized_phone = normalize_customer_phone(phone)

    if normalized_phone is None:
        return CustomerContext(customer=None, previous_orders=[])

    customer = storage.get_customer_by_phone(
        normalized_phone,
        tenant_id=tenant_id,
    )

    if customer is None:
        return CustomerContext(customer=None, previous_orders=[])

    previous_orders = storage.get_customer_order_history(
        customer.customer_id,
        tenant_id,
        limit=limit,
    )

    return CustomerContext(
        customer=customer,
        previous_orders=previous_orders,
    )


def format_new_order_customer_context(context: CustomerContext) -> str:
    if context.customer is None:
        return "Cliente nuevo"

    count = context.previous_order_count
    suffix = "pedido anterior" if count == 1 else "pedidos anteriores"

    return f"Cliente conocido: {context.customer.customer_name} - {count} {suffix}"


def format_today_order_customer_badge(context: CustomerContext) -> str:
    if context.customer is None:
        return "First order"

    count = context.previous_order_count

    if count <= 1:
        return "First order"

    return f"Repeat customer ({count} orders)"