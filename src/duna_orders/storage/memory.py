from duna_orders.domain.models import Customer, Order, Product, StockMovement
from duna_orders.storage.base import StorageInterface


class InMemoryStorage(StorageInterface):
    def __init__(self) -> None:
        self._products: dict[str, Product] = {}
        self._customers: dict[str, Customer] = {}
        self._orders: dict[str, Order] = {}
        self._stock_movements: dict[str, StockMovement] = {}

    def list_products(self, *, active_only: bool = False) -> list[Product]:
        products = list(self._products.values())

        if active_only:
            products = [product for product in products if product.active]

        return [product.model_copy(deep=True) for product in products]

    def get_product(self, product_id: str) -> Product | None:
        product = self._products.get(product_id)
        return product.model_copy(deep=True) if product else None

    def save_product(self, product: Product) -> None:
        self._products[product.product_id] = product.model_copy(deep=True)

    def get_customer(self, customer_id: str) -> Customer | None:
        customer = self._customers.get(customer_id)
        return customer.model_copy(deep=True) if customer else None

    def save_customer(self, customer: Customer) -> None:
        self._customers[customer.customer_id] = customer.model_copy(deep=True)

    def save_order(self, order: Order) -> None:
        self._orders[order.order_id] = order.model_copy(deep=True)

    def get_order(self, order_id: str) -> Order | None:
        order = self._orders.get(order_id)
        return order.model_copy(deep=True) if order else None

    def list_orders(self) -> list[Order]:
        return [order.model_copy(deep=True) for order in self._orders.values()]

    def save_stock_movement(self, movement: StockMovement) -> None:
        self._stock_movements[movement.stock_movement_id] = movement.model_copy(deep=True)

    def list_stock_movements(self, *, product_id: str | None = None) -> list[StockMovement]:
        movements = list(self._stock_movements.values())

        if product_id is not None:
            movements = [
                movement
                for movement in movements
                if movement.product_id == product_id
            ]

        return [movement.model_copy(deep=True) for movement in movements]