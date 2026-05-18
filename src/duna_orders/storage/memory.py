from datetime import datetime

from duna_orders.domain.models import (
    Customer,
    Order,
    ParseLogEntry,
    Product,
    StockMovement,
    utc_now,
)
from duna_orders.storage.base import StorageInterface


class InMemoryStorage(StorageInterface):
    def __init__(self) -> None:
        self._products: dict[str, Product] = {}
        self._customers: dict[str, Customer] = {}
        self._orders: dict[str, Order] = {}
        self._stock_movements: list[StockMovement] = []
        self._parse_logs: list[ParseLogEntry] = []
    def list_products(self, *, active_only: bool = True) -> list[Product]:
        products = list(self._products.values())

        if active_only:
            products = [product for product in products if product.active]

        return [product.model_copy(deep=True) for product in products]

    def get_product(self, product_id: str) -> Product | None:
        product = self._products.get(product_id)
        return product.model_copy(deep=True) if product else None

    def upsert_product(self, product: Product) -> Product:
        persisted = product.model_copy(deep=True)
        self._products[product.product_id] = persisted
        return persisted.model_copy(deep=True)

    def list_customers(self) -> list[Customer]:
        return [customer.model_copy(deep=True) for customer in self._customers.values()]

    def get_customer(self, customer_id: str) -> Customer | None:
        customer = self._customers.get(customer_id)
        return customer.model_copy(deep=True) if customer else None

    def get_customer_by_phone(self, phone: str) -> Customer | None:
        normalized_phone = phone.strip()

        if not normalized_phone:
            return None

        for customer in self._customers.values():
            if customer.customer_phone and customer.customer_phone.strip() == normalized_phone:
                return customer.model_copy(deep=True)

        return None

    def create_customer(self, customer: Customer) -> Customer:
        if customer.customer_id in self._customers:
            raise ValueError(f"Customer already exists: {customer.customer_id}")

        persisted = customer.model_copy(deep=True)
        self._customers[customer.customer_id] = persisted
        return persisted.model_copy(deep=True)

    def create_order(self, order: Order) -> Order:
        if order.order_id in self._orders:
            raise ValueError(f"Order already exists: {order.order_id}")

        persisted = order.model_copy(deep=True)
        self._orders[order.order_id] = persisted
        return persisted.model_copy(deep=True)

    def get_order(self, order_id: str) -> Order | None:
        order = self._orders.get(order_id)
        return order.model_copy(deep=True) if order else None

    def list_orders(
        self,
        *,
        status: str | None = None,
        since: datetime | None = None,
    ) -> list[Order]:
        orders = list(self._orders.values())

        if status is not None:
            orders = [order for order in orders if order.status == status]

        if since is not None:
            orders = [order for order in orders if order.created_at >= since]

        return [order.model_copy(deep=True) for order in orders]

    def update_order_status(
        self,
        order_id: str,
        status: str,
        confirmed_at: datetime | None = None,
    ) -> Order:
        order = self._orders.get(order_id)

        if order is None:
            raise KeyError(f"Order not found: {order_id}")

        updates = {
            "status": status,
            "updated_at": utc_now(),
        }

        if confirmed_at is not None:
            updates["confirmed_at"] = confirmed_at

        updated_order = order.model_copy(update=updates, deep=True)
        self._orders[order_id] = updated_order

        return updated_order.model_copy(deep=True)

    def append_stock_movement(self, movement: StockMovement) -> StockMovement:
        duplicated = any(
            existing.stock_movement_id == movement.stock_movement_id
            for existing in self._stock_movements
        )

        if duplicated:
            raise ValueError(f"Stock movement already exists: {movement.stock_movement_id}")

        persisted = movement.model_copy(deep=True)
        self._stock_movements.append(persisted)

        return persisted.model_copy(deep=True)

    def list_stock_movements(
        self,
        *,
        product_id: str | None = None,
    ) -> list[StockMovement]:
        movements = self._stock_movements

        if product_id is not None:
            movements = [
                movement
                for movement in movements
                if movement.product_id == product_id
            ]

        return [movement.model_copy(deep=True) for movement in movements]
    
    def append_parse_log(self, entry: ParseLogEntry) -> ParseLogEntry:
        if any(existing.parse_id == entry.parse_id for existing in self._parse_logs):
            raise ValueError(f"Parse log {entry.parse_id} already exists")

        persisted = entry.model_copy(deep=True)
        self._parse_logs.append(persisted)

        return persisted.model_copy(deep=True) 