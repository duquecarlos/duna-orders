from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from duna_orders.domain.models import Order, OrderItem
from duna_orders.storage.postgres import PostgresStorage
from duna_orders.storage.postgres_base import Base
from duna_orders.storage.postgres_session import make_engine, make_session_factory
from tests.conftest import DEFAULT_TEST_TENANT_ID


@pytest.fixture
def postgres_storage(tmp_path) -> PostgresStorage:
    database_path = tmp_path / "postgres_storage_orders_test.db"
    engine = make_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)

    return PostgresStorage(make_session_factory(engine))


def make_order(
    *,
    order_id: str = "ord_test",
    product_id: str | None = "prd_test",
    status: str = "draft",
    created_at: datetime | None = None,
    customer_id: str | None = None,
    tenant_id: str = DEFAULT_TEST_TENANT_ID,
) -> Order:
    item = OrderItem(
        tenant_id=tenant_id,
        order_item_id=f"oit_{order_id}",
        order_id=order_id,
        product_id=product_id,
        product_name_snapshot="Empanada",
        unit_snapshot="unidad",
        quantity=Decimal("2"),
        unit_price_snapshot=Decimal("3000"),
        line_total=Decimal("6000"),
        modifications="sin cebolla",
        validation_status="ok",
    )

    return Order(
        tenant_id=tenant_id,
        order_id=order_id,
        created_at=created_at or datetime.now(timezone.utc),
        customer_id=customer_id,
        customer_phone_snapshot="3001234567",
        raw_message="Quiero 2 empanadas",
        status=status,
        items=[item],
        subtotal=Decimal("6000"),
        delivery_fee=Decimal("0"),
        packaging_fee=Decimal("1000"),
        total=Decimal("7000"),
        fulfillment_type="delivery",
        delivery_zone="zona_demo",
        customer_notes="Dejar en portería",
        payment_method="nequi",
    )


def test_create_order_persists_items_and_get_order(postgres_storage: PostgresStorage):
    order = make_order()

    postgres_storage.create_order(order)

    saved_order = postgres_storage.get_order(order.order_id)

    assert saved_order is not None
    assert saved_order.customer_phone_snapshot == "3001234567"
    assert saved_order.status == "draft"
    assert len(saved_order.items) == 1
    assert saved_order.items[0].product_name_snapshot == "Empanada"
    assert saved_order.items[0].order_item_id == order.items[0].order_item_id
    assert saved_order.items[0].modifications == "sin cebolla"
    assert saved_order.packaging_fee == Decimal("1000")
    assert saved_order.total == Decimal("7000")
    assert saved_order.fulfillment_type == "delivery"
    assert saved_order.delivery_zone == "zona_demo"
    assert saved_order.customer_notes == "Dejar en portería"
    assert saved_order.payment_method == "nequi"
    assert postgres_storage.get_order("missing") is None


def test_create_order_raises_on_duplicate(postgres_storage: PostgresStorage):
    order = make_order()

    postgres_storage.create_order(order)

    with pytest.raises(ValueError):
        postgres_storage.create_order(order)


def test_update_order_status_confirms_and_sets_timestamp(
    postgres_storage: PostgresStorage,
):
    order = make_order()
    confirmed_at = datetime.now(timezone.utc).replace(microsecond=123456)

    postgres_storage.create_order(order)

    updated_order = postgres_storage.update_order_status(
        order.order_id,
        "confirmed",
        confirmed_at=confirmed_at,
    )
    saved_order = postgres_storage.get_order(order.order_id)

    assert updated_order.status == "confirmed"
    assert updated_order.confirmed_at == confirmed_at
    assert updated_order.status_updated_at == confirmed_at
    assert saved_order is not None
    assert saved_order.confirmed_at == confirmed_at
    assert saved_order.status_updated_at == confirmed_at


def test_update_order_status_sets_status_updated_at_without_confirmed_at(
    postgres_storage: PostgresStorage,
):
    order = make_order(status="confirmed")
    changed_at = datetime.now(timezone.utc).replace(microsecond=654321)

    postgres_storage.create_order(order)

    updated_order = postgres_storage.update_order_status(
        order.order_id,
        "in_preparation",
        status_updated_at=changed_at,
    )
    saved_order = postgres_storage.get_order(order.order_id)

    assert updated_order.status == "in_preparation"
    assert updated_order.confirmed_at is None
    assert updated_order.status_updated_at == changed_at
    assert saved_order is not None
    assert saved_order.status == "in_preparation"
    assert saved_order.confirmed_at is None
    assert saved_order.status_updated_at == changed_at


def test_update_order_status_raises_on_missing_order(
    postgres_storage: PostgresStorage,
):
    with pytest.raises(KeyError):
        postgres_storage.update_order_status("missing", "confirmed")


def test_list_orders_filters_by_status_and_since(postgres_storage: PostgresStorage):
    old_date = datetime.now(timezone.utc) - timedelta(days=2)
    new_date = datetime.now(timezone.utc)

    old_draft = make_order(
        order_id="ord_old_draft",
        status="draft",
        created_at=old_date,
    )
    new_draft = make_order(
        order_id="ord_new_draft",
        status="draft",
        created_at=new_date,
    )
    confirmed = make_order(
        order_id="ord_confirmed",
        status="confirmed",
        created_at=new_date,
    )

    postgres_storage.create_order(old_draft)
    postgres_storage.create_order(new_draft)
    postgres_storage.create_order(confirmed)

    draft_ids = {
        order.order_id
        for order in postgres_storage.list_orders(status="draft")
    }
    recent_ids = {
        order.order_id
        for order in postgres_storage.list_orders(since=new_date - timedelta(minutes=1))
    }

    assert draft_ids == {"ord_old_draft", "ord_new_draft"}
    assert recent_ids == {"ord_new_draft", "ord_confirmed"}


def test_get_customer_order_history_filters_by_customer_tenant_and_limit(
    postgres_storage: PostgresStorage,
):
    customer_id = "cus_history"

    old_order = make_order(
        order_id="ord_old",
        customer_id=customer_id,
        created_at=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
    )
    newest_order = make_order(
        order_id="ord_newest",
        customer_id=customer_id,
        created_at=datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc),
    )
    middle_order = make_order(
        order_id="ord_middle",
        customer_id=customer_id,
        created_at=datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc),
    )
    other_customer_order = make_order(
        order_id="ord_other_customer",
        customer_id="cus_other",
        created_at=datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc),
    )
    other_tenant_order = make_order(
        order_id="ord_other_tenant",
        customer_id=customer_id,
        tenant_id="other-tenant",
        created_at=datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc),
    )

    postgres_storage.create_order(old_order)
    postgres_storage.create_order(newest_order)
    postgres_storage.create_order(middle_order)
    postgres_storage.create_order(other_customer_order)
    postgres_storage.create_order(other_tenant_order)

    history = postgres_storage.get_customer_order_history(
        customer_id,
        DEFAULT_TEST_TENANT_ID,
        limit=2,
    )

    assert [order.order_id for order in history] == [
        "ord_newest",
        "ord_middle",
    ]
