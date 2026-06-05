from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session, selectinload

from duna_orders.domain.models import (
    Customer,
    Order,
    OrderItem,
    ParseLogEntry,
    Product,
    StockMovement,
    utc_now,
)
from duna_orders.demo_dataset import DemoDataset
from duna_orders.domain.phone import normalize_customer_phone
from duna_orders.storage.base import StorageInterface
from duna_orders.storage.postgres_models import (
    CustomerRow,
    OrderItemRow,
    OrderRow,
    ParseLogRow,
    ProductRow,
    StockMovementRow,
)
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
        with session_scope(self._session_factory) as session:
            existing = session.get(OrderRow, order.order_id)

            if existing is not None:
                raise ValueError(f"Order already exists: {order.order_id}")

            row = _order_to_row(order)
            session.add(row)
            session.flush()

            return _order_from_row(row)

    def get_order(self, order_id: str) -> Order | None:
        with session_scope(self._session_factory) as session:
            row = session.scalar(
                select(OrderRow)
                .options(selectinload(OrderRow.items))
                .where(OrderRow.order_id == order_id)
            )

            return _order_from_row(row) if row is not None else None

    def list_orders(
        self,
        *,
        status: str | None = None,
        since: datetime | None = None,
    ) -> list[Order]:
        with session_scope(self._session_factory) as session:
            statement = select(OrderRow).options(selectinload(OrderRow.items))

            if status is not None:
                statement = statement.where(OrderRow.status == status)

            if since is not None:
                statement = statement.where(OrderRow.created_at >= since)

            statement = statement.order_by(OrderRow.order_id)

            rows = session.scalars(statement).all()

            return [_order_from_row(row) for row in rows]

    def get_customer_order_history(
        self,
        customer_id: str,
        tenant_id: str,
        *,
        limit: int = 10,
    ) -> list[Order]:
        with session_scope(self._session_factory) as session:
            rows = session.scalars(
                select(OrderRow)
                .options(selectinload(OrderRow.items))
                .where(OrderRow.tenant_id == tenant_id)
                .where(OrderRow.customer_id == customer_id)
                .order_by(OrderRow.created_at.desc())
                .limit(limit)
            ).all()

            return [_order_from_row(row) for row in rows]

    def update_order_status(
        self,
        order_id: str,
        status: str,
        confirmed_at: datetime | None = None,
        status_updated_at: datetime | None = None,
    ) -> Order:
        with session_scope(self._session_factory) as session:
            row = session.scalar(
                select(OrderRow)
                .options(selectinload(OrderRow.items))
                .where(OrderRow.order_id == order_id)
            )

            if row is None:
                raise KeyError(f"Order not found: {order_id}")

            now = utc_now()
            row.status = status
            row.updated_at = now
            row.status_updated_at = status_updated_at or confirmed_at or now

            if confirmed_at is not None:
                row.confirmed_at = confirmed_at

            session.flush()

            return _order_from_row(row)

    def append_stock_movement(self, movement: StockMovement) -> StockMovement:
        with session_scope(self._session_factory) as session:
            existing = session.get(StockMovementRow, movement.stock_movement_id)

            if existing is not None:
                raise ValueError(f"Stock movement already exists: {movement.stock_movement_id}")

            row = _stock_movement_to_row(movement)
            session.add(row)
            session.flush()

            return _stock_movement_from_row(row)

    def append_parse_log(self, entry: ParseLogEntry) -> ParseLogEntry:
        with session_scope(self._session_factory) as session:
            existing = session.get(ParseLogRow, entry.parse_id)

            if existing is not None:
                raise ValueError(f"Parse log {entry.parse_id} already exists")

            row = _parse_log_to_row(entry)
            session.add(row)
            session.flush()

            return _parse_log_from_row(row)

    def list_stock_movements(
        self,
        *,
        product_id: str | None = None,
    ) -> list[StockMovement]:
        with session_scope(self._session_factory) as session:
            statement = select(StockMovementRow).order_by(StockMovementRow.stock_movement_id)

            if product_id is not None:
                statement = statement.where(StockMovementRow.product_id == product_id)

            rows = session.scalars(statement).all()

            return [_stock_movement_from_row(row) for row in rows]
    def bulk_create_products(
        self,
        products: list[Product],
        *,
        session: Session | None = None,
    ) -> int:
        """Bulk seeding/migration utility. NOT part of StorageInterface.

        Do not call from application code.
        """
        if session is not None:
            return _bulk_insert_products(session, products)

        with session_scope(self._session_factory) as scoped_session:
            return _bulk_insert_products(scoped_session, products)

    def bulk_create_customers(
        self,
        customers: list[Customer],
        *,
        session: Session | None = None,
    ) -> int:
        """Bulk seeding/migration utility. NOT part of StorageInterface.

        Do not call from application code.
        """
        if session is not None:
            return _bulk_insert_customers(session, customers)

        with session_scope(self._session_factory) as scoped_session:
            return _bulk_insert_customers(scoped_session, customers)

    def bulk_create_orders(
        self,
        orders: list[Order],
        *,
        session: Session | None = None,
    ) -> int:
        """Bulk seeding/migration utility. NOT part of StorageInterface.

        Do not call from application code.
        """
        if session is not None:
            return _bulk_insert_orders(session, orders)

        with session_scope(self._session_factory) as scoped_session:
            return _bulk_insert_orders(scoped_session, orders)

    def bulk_create_order_items(
        self,
        items: list[OrderItem],
        *,
        session: Session | None = None,
    ) -> int:
        """Bulk seeding/migration utility. NOT part of StorageInterface.

        Do not call from application code.
        """
        if session is not None:
            return _bulk_insert_order_items(session, items)

        with session_scope(self._session_factory) as scoped_session:
            return _bulk_insert_order_items(scoped_session, items)

    def wipe_tenant_data(self, tenant_id: str) -> dict[str, int]:
        """Bulk seeding/migration utility. NOT part of StorageInterface.

        Do not call from application code.
        """
        with session_scope(self._session_factory) as session:
            return _wipe_tenant_data(session, tenant_id)

    def reseed_demo_dataset(self, dataset: DemoDataset) -> dict[str, int]:
        """Bulk seeding/migration utility. NOT part of StorageInterface.

        Do not call from application code.
        """
        with session_scope(self._session_factory) as session:
            _wipe_tenant_data(session, dataset.tenant_id)

            self.bulk_create_products(dataset.products, session=session)
            self.bulk_create_customers(dataset.customers, session=session)
            self.bulk_create_orders(dataset.orders, session=session)
            self.bulk_create_order_items(dataset.order_items, session=session)

            session.flush()

            return _tenant_row_counts(session, dataset.tenant_id)

def _require_tenant_id(tenant_id: str) -> str:
    if not tenant_id or not tenant_id.strip():
        raise ValueError("tenant_id is required for tenant-scoped Postgres operations")

    return tenant_id


def _bulk_insert_products(session: Session, products: list[Product]) -> int:
    if not products:
        return 0

    session.execute(
        insert(ProductRow),
        [_product_to_values(product) for product in products],
    )
    return len(products)


def _bulk_insert_customers(session: Session, customers: list[Customer]) -> int:
    if not customers:
        return 0

    session.execute(
        insert(CustomerRow),
        [_customer_to_values(customer) for customer in customers],
    )
    return len(customers)


def _bulk_insert_orders(session: Session, orders: list[Order]) -> int:
    if not orders:
        return 0

    session.execute(
        insert(OrderRow).execution_options(render_nulls=True),
        [_order_to_values(order) for order in orders],
    )
    return len(orders)


def _bulk_insert_order_items(session: Session, items: list[OrderItem]) -> int:
    if not items:
        return 0

    session.execute(
        insert(OrderItemRow),
        [_order_item_to_values(item) for item in items],
    )
    return len(items)


def _delete_tenant_rows(session: Session, row_type: type, tenant_id: str) -> int:
    scoped_tenant_id = _require_tenant_id(tenant_id)
    result = session.execute(
        delete(row_type).where(row_type.tenant_id == scoped_tenant_id)
    )

    return int(result.rowcount or 0)


def _wipe_tenant_data(session: Session, tenant_id: str) -> dict[str, int]:
    scoped_tenant_id = _require_tenant_id(tenant_id)

    return {
        "order_items": _delete_tenant_rows(session, OrderItemRow, scoped_tenant_id),
        "stock_movements": _delete_tenant_rows(
            session,
            StockMovementRow,
            scoped_tenant_id,
        ),
        "parse_log": _delete_tenant_rows(session, ParseLogRow, scoped_tenant_id),
        "orders": _delete_tenant_rows(session, OrderRow, scoped_tenant_id),
        "products": _delete_tenant_rows(session, ProductRow, scoped_tenant_id),
        "customers": _delete_tenant_rows(session, CustomerRow, scoped_tenant_id),
    }


def _tenant_count(session: Session, row_type: type, tenant_id: str) -> int:
    scoped_tenant_id = _require_tenant_id(tenant_id)

    count = session.scalar(
        select(func.count())
        .select_from(row_type)
        .where(row_type.tenant_id == scoped_tenant_id)
    )

    return int(count or 0)


def _tenant_row_counts(session: Session, tenant_id: str) -> dict[str, int]:
    scoped_tenant_id = _require_tenant_id(tenant_id)

    return {
        "products": _tenant_count(session, ProductRow, scoped_tenant_id),
        "customers": _tenant_count(session, CustomerRow, scoped_tenant_id),
        "orders": _tenant_count(session, OrderRow, scoped_tenant_id),
        "order_items": _tenant_count(session, OrderItemRow, scoped_tenant_id),
        "stock_movements": _tenant_count(session, StockMovementRow, scoped_tenant_id),
        "parse_log": _tenant_count(session, ParseLogRow, scoped_tenant_id),
    }

def _utc_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value
def _product_to_values(product: Product) -> dict[str, Any]:
    return {
        "product_id": product.product_id,
        "tenant_id": product.tenant_id,
        "product_name": product.product_name,
        "aliases": product.aliases,
        "category": product.category,
        "available_days": product.available_days,
        "unit": product.unit,
        "unit_price": product.unit_price,
        "active": product.active,
        "current_stock": product.current_stock,
        "min_stock": product.min_stock,
        "notes": product.notes,
        "created_at": product.created_at,
        "updated_at": product.updated_at,
    }


def _product_to_row(product: Product) -> ProductRow:
    return ProductRow(**_product_to_values(product))

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
        created_at=_utc_aware(row.created_at),
        updated_at=_utc_aware(row.updated_at),
    )


def _customer_to_values(customer: Customer) -> dict[str, Any]:
    return {
        "customer_id": customer.customer_id,
        "tenant_id": customer.tenant_id,
        "customer_name": customer.customer_name,
        "customer_phone": customer.customer_phone,
        "default_address": customer.default_address,
        "notes": customer.notes,
        "created_at": customer.created_at,
        "updated_at": customer.updated_at,
        "last_order_at": customer.last_order_at,
    }


def _customer_to_row(customer: Customer) -> CustomerRow:
    return CustomerRow(**_customer_to_values(customer))


def _customer_from_row(row: CustomerRow) -> Customer:
    return Customer(
        tenant_id=row.tenant_id,
        customer_id=row.customer_id,
        customer_name=row.customer_name,
        customer_phone=row.customer_phone,
        default_address=row.default_address,
        notes=row.notes,
        created_at=_utc_aware(row.created_at),
        updated_at=_utc_aware(row.updated_at),
        last_order_at=_utc_aware(row.last_order_at),
    )

def _order_to_values(order: Order) -> dict[str, Any]:
    return {
        "order_id": order.order_id,
        "tenant_id": order.tenant_id,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
        "customer_id": order.customer_id,
        "customer_name_snapshot": order.customer_name_snapshot,
        "customer_phone_snapshot": order.customer_phone_snapshot,
        "raw_message": order.raw_message,
        "status": order.status,
        "confirmed_at": order.confirmed_at,
        "status_updated_at": order.status_updated_at,
        "subtotal": order.subtotal,
        "delivery_fee": order.delivery_fee,
        "packaging_fee": order.packaging_fee,
        "total": order.total,
        "fulfillment_type": order.fulfillment_type,
        "delivery_zone": order.delivery_zone,
        "customer_notes": order.customer_notes,
        "payment_method": order.payment_method,
        "delivery_date": order.delivery_date,
        "delivery_address": order.delivery_address,
        "notes": order.notes,
        "confirmation_message": order.confirmation_message,
        "created_by": order.created_by,
    }


def _order_to_row(order: Order) -> OrderRow:
    return OrderRow(
        **_order_to_values(order),
        items=[_order_item_to_row(item) for item in order.items],
    )

def _order_from_row(row: OrderRow) -> Order:
    items = [
        _order_item_from_row(item)
        for item in sorted(row.items, key=lambda item: item.order_item_id)
    ]

    return Order(
        tenant_id=row.tenant_id,
        order_id=row.order_id,
        created_at=_utc_aware(row.created_at),
        updated_at=_utc_aware(row.updated_at),
        customer_id=row.customer_id,
        customer_name_snapshot=row.customer_name_snapshot,
        customer_phone_snapshot=row.customer_phone_snapshot,
        raw_message=row.raw_message,
        status=row.status,
        confirmed_at=_utc_aware(row.confirmed_at),
        status_updated_at=_utc_aware(row.status_updated_at),
        items=items,
        subtotal=row.subtotal,
        delivery_fee=row.delivery_fee,
        packaging_fee=row.packaging_fee,
        total=row.total,
        fulfillment_type=row.fulfillment_type,
        delivery_zone=row.delivery_zone,
        customer_notes=row.customer_notes,
        payment_method=row.payment_method,
        delivery_date=row.delivery_date,
        delivery_address=row.delivery_address,
        notes=row.notes,
        confirmation_message=row.confirmation_message,
        created_by=row.created_by,
    )


def _order_item_to_values(item: OrderItem) -> dict[str, Any]:
    return {
        "order_item_id": item.order_item_id,
        "tenant_id": item.tenant_id,
        "order_id": item.order_id,
        "product_id": item.product_id,
        "product_name_snapshot": item.product_name_snapshot,
        "unit_snapshot": item.unit_snapshot,
        "quantity": item.quantity,
        "unit_price_snapshot": item.unit_price_snapshot,
        "line_total": item.line_total,
        "modifications": item.modifications,
        "validation_status": item.validation_status,
        "notes": item.notes,
    }


def _order_item_to_row(item: OrderItem) -> OrderItemRow:
    return OrderItemRow(**_order_item_to_values(item))


def _order_item_from_row(row: OrderItemRow) -> OrderItem:
    return OrderItem(
        tenant_id=row.tenant_id,
        order_item_id=row.order_item_id,
        order_id=row.order_id,
        product_id=row.product_id,
        product_name_snapshot=row.product_name_snapshot,
        unit_snapshot=row.unit_snapshot,
        quantity=row.quantity,
        unit_price_snapshot=row.unit_price_snapshot,
        line_total=row.line_total,
        modifications=row.modifications,
        validation_status=row.validation_status,
        notes=row.notes,
    )
def _stock_movement_to_row(movement: StockMovement) -> StockMovementRow:
    return StockMovementRow(
        stock_movement_id=movement.stock_movement_id,
        tenant_id=movement.tenant_id,
        created_at=movement.created_at,
        product_id=movement.product_id,
        quantity_delta=movement.quantity_delta,
        reason=movement.reason,
        reference_id=movement.reference_id,
        notes=movement.notes,
        created_by=movement.created_by,
    )


def _stock_movement_from_row(row: StockMovementRow) -> StockMovement:
    return StockMovement(
        tenant_id=row.tenant_id,
        stock_movement_id=row.stock_movement_id,
        created_at=_utc_aware(row.created_at),
        product_id=row.product_id,
        quantity_delta=row.quantity_delta,
        reason=row.reason,
        reference_id=row.reference_id,
        notes=row.notes,
        created_by=row.created_by,
    )


def _parse_log_to_row(entry: ParseLogEntry) -> ParseLogRow:
    return ParseLogRow(
        parse_id=entry.parse_id,
        tenant_id=entry.tenant_id,
        created_at=entry.created_at,
        raw_message=entry.raw_message,
        parsed_json=entry.parsed_json,
        model=entry.model,
        prompt_version=entry.prompt_version,
        latency_ms=entry.latency_ms,
        success=entry.success,
        error=entry.error,
    )


def _parse_log_from_row(row: ParseLogRow) -> ParseLogEntry:
    return ParseLogEntry(
        tenant_id=row.tenant_id,
        parse_id=row.parse_id,
        created_at=_utc_aware(row.created_at),
        raw_message=row.raw_message,
        parsed_json=row.parsed_json,
        model=row.model,
        prompt_version=row.prompt_version,
        latency_ms=row.latency_ms,
        success=row.success,
        error=row.error,
    )