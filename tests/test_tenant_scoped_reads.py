from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from duna_orders.services.dashboard_read_scenario import (
    run_locked_dashboard_read_scenario,
)
from duna_orders.services.tenant_scoped_reads import TenantScopedReadService
from duna_orders.storage.base import StorageInterface
from duna_orders.storage.memory import InMemoryStorage
from duna_orders.storage.postgres import PostgresStorage
from duna_orders.storage.postgres_base import Base
from duna_orders.storage.postgres_session import make_engine, make_session_factory
from tests.test_storage_contract import make_customer, make_order, make_product


TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
NOW = datetime(2026, 5, 26, 15, 0, tzinfo=timezone.utc)


def _product(
    run_token: str,
    *,
    tenant_id: str,
    product_id: str,
    active: bool = True,
):
    return make_product(
        run_token,
        product_id=product_id,
        active=active,
    ).model_copy(update={"tenant_id": tenant_id}, deep=True)


def _seed_two_tenant_fixture(storage: StorageInterface) -> None:
    for product in [
        _product(
            "scoped_a_",
            tenant_id=TENANT_A,
            product_id="scoped_a_prd_active",
        ),
        _product(
            "scoped_a_",
            tenant_id=TENANT_A,
            product_id="scoped_a_prd_inactive",
            active=False,
        ),
        _product(
            "scoped_b_",
            tenant_id=TENANT_B,
            product_id="scoped_b_prd_active",
        ),
    ]:
        storage.upsert_product(product)

    for customer in [
        make_customer(
            "scoped_a_",
            customer_id="scoped_a_cus",
            tenant_id=TENANT_A,
        ),
        make_customer(
            "scoped_b_",
            customer_id="scoped_b_cus",
            tenant_id=TENANT_B,
        ),
    ]:
        storage.create_customer(customer)

    for order in [
        make_order(
            "scoped_a_",
            order_id="scoped_a_ord_draft",
            product_id="scoped_a_prd_active",
            status="draft",
            created_at=NOW,
            customer_id="scoped_a_cus",
            tenant_id=TENANT_A,
        ),
        make_order(
            "scoped_a_",
            order_id="scoped_a_ord_confirmed",
            product_id="scoped_a_prd_active",
            status="confirmed",
            created_at=NOW - timedelta(days=1),
            customer_id="scoped_a_cus",
            tenant_id=TENANT_A,
        ),
        make_order(
            "scoped_b_",
            order_id="scoped_b_ord_draft",
            product_id="scoped_b_prd_active",
            status="draft",
            created_at=NOW,
            customer_id="scoped_b_cus",
            tenant_id=TENANT_B,
        ),
    ]:
        storage.create_order(order)


@pytest.fixture(params=["memory", "postgres"])
def scoped_storage(request, tmp_path) -> StorageInterface:
    if request.param == "memory":
        return InMemoryStorage()

    database_path = tmp_path / "tenant_scoped_reads.db"
    engine = make_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    return PostgresStorage(make_session_factory(engine))


def test_scoped_reads_reject_empty_tenant_id(scoped_storage: StorageInterface) -> None:
    service = TenantScopedReadService(scoped_storage)

    with pytest.raises(ValueError, match="tenant_id is required"):
        service.list_orders(tenant_id="")

    with pytest.raises(ValueError, match="tenant_id is required"):
        service.list_products(tenant_id="   ")


def test_scoped_reads_require_tenant_id_keyword(
    scoped_storage: StorageInterface,
) -> None:
    service = TenantScopedReadService(scoped_storage)

    with pytest.raises(TypeError):
        service.list_customers()


def test_scoped_reads_return_only_requested_tenant_rows(
    scoped_storage: StorageInterface,
) -> None:
    _seed_two_tenant_fixture(scoped_storage)
    service = TenantScopedReadService(scoped_storage)

    orders = service.list_orders(tenant_id=TENANT_A)
    products = service.list_products(tenant_id=TENANT_A, active_only=False)
    customers = service.list_customers(tenant_id=TENANT_A)
    tenant_b_order = service.get_order(
        tenant_id=TENANT_A,
        order_id="scoped_b_ord_draft",
    )

    assert {order.order_id for order in orders} == {
        "scoped_a_ord_draft",
        "scoped_a_ord_confirmed",
    }
    assert {order.tenant_id for order in orders} == {TENANT_A}
    assert tenant_b_order is None
    assert {product.product_id for product in products} == {
        "scoped_a_prd_active",
        "scoped_a_prd_inactive",
    }
    assert {product.tenant_id for product in products} == {TENANT_A}
    assert [customer.customer_id for customer in customers] == ["scoped_a_cus"]
    assert {customer.tenant_id for customer in customers} == {TENANT_A}


def test_scoped_reads_preserve_order_filters(
    scoped_storage: StorageInterface,
) -> None:
    _seed_two_tenant_fixture(scoped_storage)
    service = TenantScopedReadService(scoped_storage)

    orders = service.list_orders(
        tenant_id=TENANT_A,
        status="draft",
        since=NOW - timedelta(hours=1),
    )

    assert [order.order_id for order in orders] == ["scoped_a_ord_draft"]


def test_scoped_reads_preserve_product_active_filter(
    scoped_storage: StorageInterface,
) -> None:
    _seed_two_tenant_fixture(scoped_storage)
    service = TenantScopedReadService(scoped_storage)

    active_products = service.list_products(tenant_id=TENANT_A)
    all_products = service.list_products(tenant_id=TENANT_A, active_only=False)

    assert [product.product_id for product in active_products] == [
        "scoped_a_prd_active"
    ]
    assert {product.product_id for product in all_products} == {
        "scoped_a_prd_active",
        "scoped_a_prd_inactive",
    }


def test_dashboard_read_scenario_excludes_other_tenant_rows(
    scoped_storage: StorageInterface,
) -> None:
    _seed_two_tenant_fixture(scoped_storage)

    result = run_locked_dashboard_read_scenario(
        scoped_storage,
        tenant_id=TENANT_A,
        now=NOW,
        timezone_name="America/Bogota",
    )

    assert {order.order_id for order in result.orders} == {
        "scoped_a_ord_draft",
        "scoped_a_ord_confirmed",
    }
    assert {item.tenant_id for item in result.order_items} == {TENANT_A}
    assert [customer.customer_id for customer in result.customers] == ["scoped_a_cus"]
    assert {product.product_id for product in result.products} == {
        "scoped_a_prd_active",
        "scoped_a_prd_inactive",
    }


def test_dashboard_read_scenario_preserves_single_tenant_data() -> None:
    storage = InMemoryStorage()
    storage.upsert_product(
        _product(
            "single_",
            tenant_id=TENANT_A,
            product_id="single_prd",
        )
    )
    storage.create_customer(
        make_customer(
            "single_",
            customer_id="single_cus",
            tenant_id=TENANT_A,
        )
    )
    storage.create_order(
        make_order(
            "single_",
            order_id="single_ord",
            product_id="single_prd",
            created_at=NOW,
            customer_id="single_cus",
            tenant_id=TENANT_A,
        ).model_copy(update={"total": Decimal("7000")}, deep=True)
    )

    result = run_locked_dashboard_read_scenario(
        storage,
        tenant_id=TENANT_A,
        now=NOW,
        timezone_name="America/Bogota",
    )

    assert [order.order_id for order in result.orders] == ["single_ord"]
    assert [item.order_id for item in result.order_items] == ["single_ord"]
    assert [customer.customer_id for customer in result.customers] == ["single_cus"]
    assert [product.product_id for product in result.products] == ["single_prd"]
