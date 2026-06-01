from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from duna_orders.domain.models import Customer, Order, ParseLogEntry, Product, StockMovement
from duna_orders.domain.phone import normalize_customer_phone
from duna_orders.storage.base import StorageInterface
from duna_orders.storage.postgres_models import CustomerRow, ProductRow
from duna_orders.storage.postgres_session import session_scope


class PostgresStorage(StorageInterface):
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def list_products(self, *, active_only: bool = True) -> list[Product]:
        with session_scope(self._session_factory) as session:
            statement = select(ProductRow).order_by(ProductRow.product_id)

            if active_only:
                statement = statement.where(ProductRow.active.is_(True))

            rows = session.scalars(statement).all()

            return [_product_from_row(row) for row in rows]

    def get_product(self, product_id: str) -> Product | None:
        with session_scope(self._session_factory) as session:
            row = session.get(ProductRow, product_id)

            return _product_from_row(row) if row is not None else None

    def upsert_product(self, product: Product) -> Product:
        with session_scope(self._session_factory) as session:
            row = session.get(ProductRow, product.product_id)

            if row is None:
                row = _product_to_row(product)
                session.add(row)
            else:
                _update_product_row(row, product)

            session.flush()

            return _product_from_row(row)

    def list_customers(self) -> list[Customer]:
        with session_scope(self._session_factory) as session:
            rows = session.scalars(
                select(CustomerRow).order_by(CustomerRow.customer_id)
            ).all()

            return [_customer_from_row(row) for row in rows]

    def get_customer(self, customer_id: str) -> Customer | None:
        with session_scope(self._session_factory) as session:
            row = session.get(CustomerRow, customer_id)

            return _customer_from_row(row) if row is not None else None

    def get_customer_by_phone(
        self,
        phone: str,
        *,
        tenant_id: str | None = None,
    ) -> Customer | None:
        normalized_phone = normalize_customer_phone(phone)

        if normalized_phone is None:
            return None

        with session_scope(self._session_factory) as session:
            statement = select(CustomerRow).order_by(CustomerRow.customer_id)

            if tenant_id is not None:
                statement = statement.where(CustomerRow.tenant_id == tenant_id)

            rows = session.scalars(statement).all()

            for row in rows:
                if normalize_customer_phone(row.customer_phone) == normalized_phone:
                    return _customer_from_row(row)

            return None

    def create_customer(self, customer: Customer) -> Customer:
        with session_scope(self._session_factory) as session:
            existing = session.get(CustomerRow, customer.customer_id)

            if existing is not None:
                raise ValueError(f"Customer already exists: {customer.customer_id}")

            row = _customer_to_row(customer)
            session.add(row)
            session.flush()

            return _customer_from_row(row)

    def create_order(self, order: Order) -> Order:
        raise NotImplementedError("Postgres order persistence is not implemented in M8.1B-2A.")

    def get_order(self, order_id: str) -> Order | None:
        raise NotImplementedError("Postgres order persistence is not implemented in M8.1B-2A.")

    def list_orders(
        self,
        *,
        status: str | None = None,
        since: datetime | None = None,
    ) -> list[Order]:
        raise NotImplementedError("Postgres order persistence is not implemented in M8.1B-2A.")

    def get_customer_order_history(
        self,
        customer_id: str,
        tenant_id: str,
        *,
        limit: int = 10,
    ) -> list[Order]:
        raise NotImplementedError("Postgres order history is not implemented in M8.1B-2A.")

    def update_order_status(
        self,
        order_id: str,
        status: str,
        confirmed_at: datetime | None = None,
        status_updated_at: datetime | None = None,
    ) -> Order:
        raise NotImplementedError("Postgres order status updates are not implemented in M8.1B-2A.")

    def append_stock_movement(self, movement: StockMovement) -> StockMovement:
        raise NotImplementedError("Postgres stock movements are not implemented in M8.1B-2A.")

    def append_parse_log(self, entry: ParseLogEntry) -> ParseLogEntry:
        raise NotImplementedError("Postgres parse logs are not implemented in M8.1B-2A.")

    def list_stock_movements(
        self,
        *,
        product_id: str | None = None,
    ) -> list[StockMovement]:
        raise NotImplementedError("Postgres stock movements are not implemented in M8.1B-2A.")


def _product_to_row(product: Product) -> ProductRow:
    return ProductRow(
        product_id=product.product_id,
        tenant_id=product.tenant_id,
        product_name=product.product_name,
        aliases=product.aliases,
        category=product.category,
        available_days=product.available_days,
        unit=product.unit,
        unit_price=product.unit_price,
        active=product.active,
        current_stock=product.current_stock,
        min_stock=product.min_stock,
        notes=product.notes,
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


def _update_product_row(row: ProductRow, product: Product) -> None:
    row.tenant_id = product.tenant_id
    row.product_name = product.product_name
    row.aliases = product.aliases
    row.category = product.category
    row.available_days = product.available_days
    row.unit = product.unit
    row.unit_price = product.unit_price
    row.active = product.active
    row.current_stock = product.current_stock
    row.min_stock = product.min_stock
    row.notes = product.notes
    row.created_at = product.created_at
    row.updated_at = product.updated_at


def _product_from_row(row: ProductRow) -> Product:
    return Product(
        tenant_id=row.tenant_id,
        product_id=row.product_id,
        product_name=row.product_name,
        aliases=list(row.aliases or []),
        category=row.category,
        available_days=row.available_days,
        unit=row.unit,
        unit_price=row.unit_price,
        active=row.active,
        current_stock=row.current_stock,
        min_stock=row.min_stock,
        notes=row.notes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _customer_to_row(customer: Customer) -> CustomerRow:
    return CustomerRow(
        customer_id=customer.customer_id,
        tenant_id=customer.tenant_id,
        customer_name=customer.customer_name,
        customer_phone=customer.customer_phone,
        default_address=customer.default_address,
        notes=customer.notes,
        created_at=customer.created_at,
        updated_at=customer.updated_at,
        last_order_at=customer.last_order_at,
    )


def _customer_from_row(row: CustomerRow) -> Customer:
    return Customer(
        tenant_id=row.tenant_id,
        customer_id=row.customer_id,
        customer_name=row.customer_name,
        customer_phone=row.customer_phone,
        default_address=row.default_address,
        notes=row.notes,
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_order_at=row.last_order_at,
    )