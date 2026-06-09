from decimal import Decimal

import pytest

from duna_orders.domain.models import Customer, Product
from duna_orders.storage.postgres import PostgresStorage
from duna_orders.storage.postgres_base import Base
from duna_orders.storage.postgres_session import make_engine, make_session_factory
from tests.conftest import DEFAULT_TEST_TENANT_ID


@pytest.fixture
def postgres_storage(tmp_path) -> PostgresStorage:
    database_path = tmp_path / "postgres_storage_test.db"
    engine = make_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)

    return PostgresStorage(make_session_factory(engine))


def make_product(
    *,
    product_id: str = "prd_test",
    product_name: str = "Empanada",
    active: bool = True,
    unit_price: Decimal = Decimal("3000"),
    current_stock: Decimal = Decimal("10"),
) -> Product:
    return Product(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        product_id=product_id,
        product_name=product_name,
        aliases=["emp"],
        category="entradas",
        available_days=["monday", "tuesday"],
        unit="unit",
        unit_price=unit_price,
        active=active,
        current_stock=current_stock,
        min_stock=Decimal("1"),
        notes="test notes",
    )


def make_customer(
    *,
    customer_id: str = "cus_test",
    phone: str | None = "3001234567",
    tenant_id: str = DEFAULT_TEST_TENANT_ID,
) -> Customer:
    return Customer(
        tenant_id=tenant_id,
        customer_id=customer_id,
        customer_name="Cliente Test",
        customer_phone=phone,
        default_address="Calle 1 # 2-3",
        notes="Cliente frecuente",
    )


def test_product_upsert_get_and_list_active_only(postgres_storage: PostgresStorage):
    active_product = make_product(product_id="prd_active", active=True)
    inactive_product = make_product(product_id="prd_inactive", active=False)

    postgres_storage.upsert_product(active_product)
    postgres_storage.upsert_product(inactive_product)

    saved_product = postgres_storage.get_product("prd_active")
    active_ids = {
        product.product_id for product in postgres_storage.unscoped_list_products()
    }
    all_ids = {
        product.product_id
        for product in postgres_storage.unscoped_list_products(active_only=False)
    }

    assert saved_product is not None
    assert saved_product.product_name == "Empanada"
    assert saved_product.aliases == ["emp"]
    assert saved_product.available_days == ["monday", "tuesday"]
    assert saved_product.unit_price == Decimal("3000")
    assert "prd_active" in active_ids
    assert "prd_inactive" not in active_ids
    assert all_ids == {"prd_active", "prd_inactive"}
    assert postgres_storage.get_product("missing") is None


def test_product_upsert_replaces_existing_product(postgres_storage: PostgresStorage):
    postgres_storage.upsert_product(
        make_product(
            product_id="prd_replace",
            product_name="Empanada",
            unit_price=Decimal("3000"),
        )
    )

    postgres_storage.upsert_product(
        make_product(
            product_id="prd_replace",
            product_name="Arepa",
            unit_price=Decimal("5000"),
            current_stock=Decimal("4"),
        )
    )

    saved_product = postgres_storage.get_product("prd_replace")
    matching = [
        product
        for product in postgres_storage.unscoped_list_products(active_only=False)
        if product.product_id == "prd_replace"
    ]

    assert saved_product is not None
    assert saved_product.product_name == "Arepa"
    assert saved_product.unit_price == Decimal("5000")
    assert saved_product.current_stock == Decimal("4")
    assert len(matching) == 1


def test_customer_create_get_and_list(postgres_storage: PostgresStorage):
    customer = make_customer()

    postgres_storage.create_customer(customer)

    saved_customer = postgres_storage.get_customer(customer.customer_id)
    customers = postgres_storage.unscoped_list_customers()

    assert saved_customer is not None
    assert saved_customer.customer_name == "Cliente Test"
    assert saved_customer.customer_phone == "3001234567"
    assert saved_customer.default_address == "Calle 1 # 2-3"
    assert [customer.customer_id for customer in customers] == ["cus_test"]
    assert postgres_storage.get_customer("missing") is None


def test_customer_create_raises_on_duplicate(postgres_storage: PostgresStorage):
    customer = make_customer()

    postgres_storage.create_customer(customer)

    with pytest.raises(ValueError):
        postgres_storage.create_customer(customer)


def test_customer_phone_lookup_normalizes_and_respects_tenant(
    postgres_storage: PostgresStorage,
):
    main_customer = make_customer(
        customer_id="cus_main",
        phone="300 123-4567",
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )
    other_customer = make_customer(
        customer_id="cus_other",
        phone="3001234567",
        tenant_id="other-tenant",
    )

    postgres_storage.create_customer(main_customer)
    postgres_storage.create_customer(other_customer)

    found_main = postgres_storage.get_customer_by_phone(
        " 3001234567 ",
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )
    found_other = postgres_storage.get_customer_by_phone(
        " 3001234567 ",
        tenant_id="other-tenant",
    )
    missing_customer = postgres_storage.get_customer_by_phone("000")

    assert found_main is not None
    assert found_main.customer_id == "cus_main"
    assert found_other is not None
    assert found_other.customer_id == "cus_other"
    assert missing_customer is None
