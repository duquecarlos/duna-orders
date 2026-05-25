from abc import ABC, abstractmethod
from datetime import datetime

from duna_orders.domain.models import (
    Customer,
    Order,
    ParseLogEntry,
    Product,
    StockMovement,
)

class StorageInterface(ABC):
    @abstractmethod
    def list_products(self, *, active_only: bool = True) -> list[Product]:
        pass

    @abstractmethod
    def get_product(self, product_id: str) -> Product | None:
        pass

    @abstractmethod
    def upsert_product(self, product: Product) -> Product:
        pass

    @abstractmethod
    def list_customers(self) -> list[Customer]:
        pass

    @abstractmethod
    def get_customer(self, customer_id: str) -> Customer | None:
        pass

    @abstractmethod
    def get_customer_by_phone(
        self,
        phone: str,
        *,
        tenant_id: str | None = None,
    ) -> Customer | None:
        pass

    @abstractmethod
    def create_customer(self, customer: Customer) -> Customer:
        pass

    @abstractmethod
    def create_order(self, order: Order) -> Order:
        pass

    @abstractmethod
    def get_order(self, order_id: str) -> Order | None:
        pass

    @abstractmethod
    def list_orders(
        self,
        *,
        status: str | None = None,
        since: datetime | None = None,
    ) -> list[Order]:
        pass

    @abstractmethod
    def get_customer_order_history(
        self,
        customer_id: str,
        tenant_id: str,
        *,
        limit: int = 10,
    ) -> list[Order]:
        pass
    @abstractmethod
    def update_order_status(
        self,
        order_id: str,
        status: str,
        confirmed_at: datetime | None = None,
        status_updated_at: datetime | None = None,
    ) -> Order:
        pass

    @abstractmethod
    def append_stock_movement(self, movement: StockMovement) -> StockMovement:
        pass

    @abstractmethod
    def append_parse_log(self, entry: ParseLogEntry) -> ParseLogEntry:
        pass

    @abstractmethod
    def list_stock_movements(
        self,
        *,
        product_id: str | None = None,
    ) -> list[StockMovement]:
        pass