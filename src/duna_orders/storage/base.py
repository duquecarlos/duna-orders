from abc import ABC, abstractmethod

from duna_orders.domain.models import Customer, Order, Product, StockMovement


class StorageInterface(ABC):
    @abstractmethod
    def list_products(self, *, active_only: bool = False) -> list[Product]:
        pass

    @abstractmethod
    def get_product(self, product_id: str) -> Product | None:
        pass

    @abstractmethod
    def save_product(self, product: Product) -> None:
        pass

    @abstractmethod
    def get_customer(self, customer_id: str) -> Customer | None:
        pass

    @abstractmethod
    def save_customer(self, customer: Customer) -> None:
        pass

    @abstractmethod
    def save_order(self, order: Order) -> None:
        pass

    @abstractmethod
    def get_order(self, order_id: str) -> Order | None:
        pass

    @abstractmethod
    def list_orders(self) -> list[Order]:
        pass

    @abstractmethod
    def save_stock_movement(self, movement: StockMovement) -> None:
        pass

    @abstractmethod
    def list_stock_movements(self, *, product_id: str | None = None) -> list[StockMovement]:
        pass