from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from duna_orders.domain.models import Product, Weekday


DEFAULT_DEMO_CATALOG_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "demo_restaurant_catalog.json"
)


class DemoCatalogLoadError(RuntimeError):
    pass


class DemoBusinessInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    business_name: str
    business_type: str
    currency: str


class CatalogProductEntry(BaseModel):
    """Tenant-less product entry as stored in the catalog JSON.

    Catalog product entries must not include tenant_id. The loader injects
    business.tenant_id into each Product during normalization. This keeps the
    JSON readable and makes mismatches loud.
    """

    model_config = ConfigDict(extra="forbid")

    product_id: str
    product_name: str
    aliases: list[str] = Field(default_factory=list)
    category: str | None = None
    available_days: list[Weekday] | None = None
    unit: str = "unit"
    unit_price: Decimal
    active: bool = True
    current_stock: Decimal = Decimal("0")
    min_stock: Decimal = Decimal("0")
    notes: str | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_product_level_tenant_id(cls, data: Any) -> Any:
        if isinstance(data, dict) and "tenant_id" in data:
            raise ValueError(
                "Catalog products must not include tenant_id; "
                "tenant_id comes from business.tenant_id."
            )
        return data


class RawDemoCatalogFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business: DemoBusinessInfo
    products: list[CatalogProductEntry]


class DemoCatalogFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business: DemoBusinessInfo
    products: list[Product]


def load_demo_catalog(path: str | Path | None = None) -> DemoCatalogFile:
    """Load and validate the demo catalog.

    Contract:
    - The JSON has a top-level business block.
    - Product entries in JSON are tenant-less and must not include tenant_id.
    - The loader is the sole source that injects business.tenant_id into each
      Product before returning the validated DemoCatalogFile.
    """

    catalog_path = Path(path) if path is not None else DEFAULT_DEMO_CATALOG_PATH

    try:
        raw_data: Any = json.loads(catalog_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DemoCatalogLoadError(f"Demo catalog file not found: {catalog_path}") from exc
    except json.JSONDecodeError as exc:
        raise DemoCatalogLoadError(
            f"Invalid JSON in demo catalog {catalog_path}: {exc.msg}"
        ) from exc

    try:
        raw_catalog = RawDemoCatalogFile.model_validate(raw_data)
        products = [
            Product.model_validate(
                {
                    **entry.model_dump(),
                    "tenant_id": raw_catalog.business.tenant_id,
                }
            )
            for entry in raw_catalog.products
        ]
        return DemoCatalogFile(
            business=raw_catalog.business,
            products=products,
        )
    except ValidationError as exc:
        raise DemoCatalogLoadError(
            f"Invalid demo catalog schema in {catalog_path}: {exc}"
        ) from exc