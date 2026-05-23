import json
from collections import Counter

import pytest

from duna_orders.demo_catalog import DemoCatalogLoadError, load_demo_catalog


DEMO_TENANT_ID = "el-fogon-colombiano"


def test_demo_catalog_loads_with_expected_shape() -> None:
    catalog = load_demo_catalog()

    assert catalog.business.tenant_id == DEMO_TENANT_ID
    assert catalog.business.business_name
    assert catalog.business.business_type == "restaurant"
    assert catalog.business.currency == "COP"
    assert len(catalog.products) == 52
    assert all(product.tenant_id == DEMO_TENANT_ID for product in catalog.products)


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


def test_demo_catalog_rejects_product_level_tenant_id(tmp_path) -> None:
    malformed_catalog = {
        "business": {
            "tenant_id": DEMO_TENANT_ID,
            "business_name": "Demo Business",
            "business_type": "restaurant",
            "currency": "COP",
        },
        "products": [
            {
                "tenant_id": "wrong-place",
                "product_id": "demo-product",
                "product_name": "Demo Product",
                "unit_price": "10000",
            }
        ],
    }

    catalog_path = tmp_path / "malformed_catalog.json"
    catalog_path.write_text(
        json.dumps(malformed_catalog),
        encoding="utf-8",
    )

    with pytest.raises(DemoCatalogLoadError, match="must not include tenant_id"):
        load_demo_catalog(catalog_path)