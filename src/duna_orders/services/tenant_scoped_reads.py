from __future__ import annotations

from datetime import datetime

from duna_orders.domain.models import Customer, Order, Product
from duna_orders.storage.base import StorageInterface


class TenantScopedReadService:
    def __init__(self, storage: StorageInterface) -> None:
        self._storage = storage

    def list_orders(
        self,
        *,
        tenant_id: str,
        status: str | None = None,
        since: datetime | None = None,
    ) -> list[Order]:
        scoped_tenant_id = _require_tenant_id(tenant_id)
        return [
            order
            for order in self._storage.list_orders(status=status, since=since)
            if order.tenant_id == scoped_tenant_id
        ]

    def get_order(
        self,
        *,
        tenant_id: str,
        order_id: str,
    ) -> Order | None:
        scoped_tenant_id = _require_tenant_id(tenant_id)
        order = self._storage.get_order(order_id)

        if order is None or order.tenant_id != scoped_tenant_id:
            return None

        return order

    def list_products(
        self,
        *,
        tenant_id: str,
        active_only: bool = True,
    ) -> list[Product]:
        scoped_tenant_id = _require_tenant_id(tenant_id)
        return [
            product
            for product in self._storage.unscoped_list_products(active_only=active_only)
            if product.tenant_id == scoped_tenant_id
        ]

    def list_customers(
        self,
        *,
        tenant_id: str,
    ) -> list[Customer]:
        scoped_tenant_id = _require_tenant_id(tenant_id)
        return [
            customer
            for customer in self._storage.unscoped_list_customers()
            if customer.tenant_id == scoped_tenant_id
        ]


def _require_tenant_id(tenant_id: str) -> str:
    if not tenant_id.strip():
        raise ValueError("tenant_id is required")

    return tenant_id
