
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from duna_orders.domain.models import Product


DEFAULT_DEMO_CATALOG_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "demo_restaurant_catalog.json"
)


class DemoCatalogLoadError(ValueError):
    pass


class DemoRestaurantInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    restaurant_id: str
    restaurant_name: str
    currency: str


class DemoCatalogFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    restaurant: DemoRestaurantInfo
    products: list[Product]


def load_demo_catalog(path: str | Path | None = None) -> DemoCatalogFile:
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
        return DemoCatalogFile.model_validate(raw_data)
    except ValidationError as exc:
        raise DemoCatalogLoadError(
            f"Invalid demo catalog schema in {catalog_path}: {exc}"
        ) from exc