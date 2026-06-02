from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic.command import upgrade
from alembic.config import Config
from sqlalchemy import text

from duna_orders.config import settings
from duna_orders.domain.models import Customer, Order, OrderItem, Product
from duna_orders.storage.postgres import PostgresStorage
from duna_orders.storage.postgres_base import Base
from duna_orders.storage.postgres_session import make_engine, make_session_factory


pytestmark = pytest.mark.live_postgres


def _require_database_url() -> str:
    if not settings.database_url:
        pytest.skip("DATABASE_URL is required for live_postgres tests")

    return settings.database_url


def _cleanup_tenant(engine, tenant_id: str) -> None:
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            if "tenant_id" in table.c:
                connection.execute(table.delete().where(table.c.tenant_id == tenant_id))


def _make_product(tenant_id: str, product_id: str) -> Product:
    return Product(
        tenant_id=tenant_id,
        product_id=product_id,
        product_name="Producto Live Postgres",
        aliases=["live postgres"],
        category="test",
        available_days=["monday"],
        unit="unit",
        unit_price=Decimal("12000"),
        active=True,
        current_stock=Decimal("20"),
        min_stock=Decimal("1"),
        notes="live_postgres smoke test",
    )


def _make_customer(tenant_id: str, customer_id: str) -> Customer:
    return Customer(
        tenant_id=tenant_id,
        customer_id=customer_id,
        customer_name="Cliente Live Postgres",
        customer_phone="+573001234567",
        default_address="Calle test # 1-2",
        notes="live_postgres smoke test",
    )


def _make_order(
    *,
    tenant_id: str,
    order_id: str,
    order_item_id: str,
    product_id: str,
    customer_id: str,
) -> Order:
    item = OrderItem(
        tenant_id=tenant_id,
        order_item_id=order_item_id,
        order_id=order_id,
        product_id=product_id,
        product_name_snapshot="Producto Live Postgres",
        unit_snapshot="unit",
        quantity=Decimal("2"),
        unit_price_snapshot=Decimal("12000"),
        line_total=Decimal("24000"),
        modifications="sin prueba extra",
        validation_status="ok",
    )

    return Order(
        tenant_id=tenant_id,
        order_id=order_id,
        created_at=datetime.now(timezone.utc),
        customer_id=customer_id,
        customer_phone_snapshot="+573001234567",
        raw_message="live postgres smoke order",
        status="draft",
        items=[item],
        subtotal=Decimal("24000"),
        delivery_fee=Decimal("0"),
        packaging_fee=Decimal("0"),
        total=Decimal("24000"),
        fulfillment_type="delivery",
        delivery_zone="live_postgres",
        customer_notes="live smoke",
    payment_method="efectivo",
    )


def test_live_postgres_alembic_upgrade_head() -> None:
    database_url = _require_database_url()

    upgrade(Config("alembic.ini"), "head")

    engine = make_engine(database_url)
    try:
        with engine.connect() as connection:
            version = connection.execute(text("select version_num from alembic_version")).scalar_one()

        assert version
    finally:
        engine.dispose()


def test_live_postgres_storage_product_customer_order_flow() -> None:
    database_url = _require_database_url()
    tenant_id = f"tenant_live_pg_{uuid4().hex}"
    product_id = f"prd_live_pg_{uuid4().hex}"
    customer_id = f"cus_live_pg_{uuid4().hex}"
    order_id = f"ord_live_pg_{uuid4().hex}"
    order_item_id = f"oit_live_pg_{uuid4().hex}"

    upgrade(Config("alembic.ini"), "head")

    engine = make_engine(database_url)
    storage = PostgresStorage(make_session_factory(engine))

    try:
        _cleanup_tenant(engine, tenant_id)

        storage.upsert_product(_make_product(tenant_id, product_id))
        storage.create_customer(_make_customer(tenant_id, customer_id))
        storage.create_order(
            _make_order(
                tenant_id=tenant_id,
                order_id=order_id,
                order_item_id=order_item_id,
                product_id=product_id,
                customer_id=customer_id,
            )
        )

        saved_product = storage.get_product(product_id)
        saved_customer = storage.get_customer(customer_id)
        saved_order = storage.get_order(order_id)

        assert saved_product is not None
        assert saved_product.product_id == product_id
        assert saved_customer is not None
        assert saved_customer.customer_id == customer_id
        assert saved_order is not None
        assert saved_order.order_id == order_id
        assert saved_order.items[0].order_item_id == order_item_id
        assert saved_order.items[0].product_id == product_id
    finally:
        _cleanup_tenant(engine, tenant_id)
        engine.dispose()