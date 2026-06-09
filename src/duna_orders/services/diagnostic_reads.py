from __future__ import annotations

from duna_orders.domain.models import Order
from duna_orders.storage.base import StorageInterface


class DiagnosticReadService:
    def __init__(self, storage: StorageInterface) -> None:
        self._storage = storage

    def get_order_for_diagnostics(self, order_id: str) -> Order | None:
        """Broad order read reserved for cross-tenant diagnostics.

        This preserves the ability to distinguish missing linked orders from
        tenant mismatches; do not use it for normal runtime reads.
        """
        return self._storage.get_order(order_id)
