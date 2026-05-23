from duna_orders.demo_catalog import load_demo_catalog
from duna_orders.domain.models import Product
from scripts.seed_demo_catalog import seed_demo_catalog_products


class FakeProductStorage:
    def __init__(self) -> None:
        self.products: list[Product] = []

    def upsert_product(self, product: Product) -> Product:
        self.products.append(product)
        return product


def test_seed_demo_catalog_products_upserts_all_products() -> None:
    catalog = load_demo_catalog()
    storage = FakeProductStorage()

    result = seed_demo_catalog_products(
        catalog=catalog,
        storage=storage,
    )

    assert result.total_products == 52
    assert result.upserted_products == 52
    assert result.dry_run is False
    assert len(storage.products) == 52
    assert {product.product_id for product in storage.products} == {
        product.product_id for product in catalog.products
    }


def test_seed_demo_catalog_products_dry_run_skips_storage() -> None:
    catalog = load_demo_catalog()
    storage = FakeProductStorage()

    result = seed_demo_catalog_products(
        catalog=catalog,
        storage=storage,
        dry_run=True,
    )

    assert result.total_products == 52
    assert result.upserted_products == 0
    assert result.dry_run is True
    assert storage.products == []
    assert all(product.tenant_id == catalog.business.tenant_id for product in storage.products)
    