from collections import Counter

from duna_orders.demo_catalog import load_demo_catalog


def test_demo_catalog_loads_with_expected_shape() -> None:
    catalog = load_demo_catalog()

    assert catalog.restaurant.restaurant_id == "el-fogon-colombiano"
    assert catalog.restaurant.restaurant_name == "El Fogón Colombiano"
    assert catalog.restaurant.currency == "COP"
    assert len(catalog.products) == 52


def test_demo_catalog_category_distribution() -> None:
    catalog = load_demo_catalog()

    assert Counter(product.category for product in catalog.products) == {
        "entradas": 6,
        "sopas": 5,
        "platos_fuertes": 12,
        "parrilla": 6,
        "acompañamientos": 6,
        "bebidas": 7,
        "postres": 4,
        "adiciones": 6,
    }


def test_demo_catalog_has_restricted_days_and_weight_variants() -> None:
    catalog = load_demo_catalog()
    product_ids = {product.product_id for product in catalog.products}

    assert any(product.available_days for product in catalog.products)
    assert "parrilla-punta-anca-200gr" in product_ids
    assert "parrilla-punta-anca-400gr" in product_ids