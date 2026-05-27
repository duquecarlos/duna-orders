"""Seed the demo restaurant catalog products into a Google Sheets target.

Usage:
    python scripts/seed_demo_catalog.py --dry-run
    python scripts/seed_demo_catalog.py --target demo
    python scripts/seed_demo_catalog.py --target runtime

Manual demo spreadsheet seed:
    1. Set GOOGLE_SHEETS_DEMO_SPREADSHEET_ID in .env.
    2. Run:
       python scripts/seed_demo_catalog.py --target demo --delay-s 2
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from duna_orders.config import settings
from duna_orders.demo_catalog import DemoCatalogFile, load_demo_catalog
from duna_orders.domain.models import Product
from duna_orders.storage.sheets import GoogleSheetsStorage


Target = Literal["demo", "runtime"]


class ProductStorage(Protocol):
    def upsert_product(self, product: Product) -> Product:
        pass


@dataclass(frozen=True)
class SeedResult:
    total_products: int
    upserted_products: int
    dry_run: bool


def seed_demo_catalog_products(
    *,
    catalog: DemoCatalogFile,
    storage: ProductStorage | None,
    dry_run: bool = False,
    delay_s: float = 0,
) -> SeedResult:
    if dry_run:
        return SeedResult(
            total_products=len(catalog.products),
            upserted_products=0,
            dry_run=True,
        )

    if storage is None:
        raise ValueError("storage is required unless dry_run=True")

    for product in catalog.products:
        storage.upsert_product(product)
        if delay_s > 0:
            time.sleep(delay_s)

    return SeedResult(
        total_products=len(catalog.products),
        upserted_products=len(catalog.products),
        dry_run=False,
    )


def _resolve_spreadsheet_id(target: str) -> str:
    if target == "demo":
        spreadsheet_id = settings.google_sheets_demo_spreadsheet_id

        if not spreadsheet_id:
            raise RuntimeError(
                "--target=demo requires GOOGLE_SHEETS_DEMO_SPREADSHEET_ID."
            )

        if (
            settings.google_sheets_spreadsheet_id
            and spreadsheet_id == settings.google_sheets_spreadsheet_id
        ):
            raise RuntimeError(
                "GOOGLE_SHEETS_DEMO_SPREADSHEET_ID must not equal "
                "GOOGLE_SHEETS_SPREADSHEET_ID."
            )

        return spreadsheet_id

    if target == "runtime":
        if not settings.google_sheets_spreadsheet_id:
            raise RuntimeError(
                "--target=runtime requires GOOGLE_SHEETS_SPREADSHEET_ID."
            )

        return settings.google_sheets_spreadsheet_id

    raise RuntimeError(f"Unsupported target: {target}")


def make_sheets_storage(*, target: str) -> GoogleSheetsStorage:
    spreadsheet_id = _resolve_spreadsheet_id(target)

    return GoogleSheetsStorage(
        spreadsheet_id=spreadsheet_id,
        credentials_path=str(settings.google_sheets_credentials_path),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed demo catalog products into a Google Sheet target."
    )
    parser.add_argument(
        "--target",
        choices=("demo", "runtime"),
        default="demo",
        help="Spreadsheet target. Defaults to demo.",
    )
    parser.add_argument(
        "--catalog-path",
        type=Path,
        default=None,
        help="Optional path to the demo catalog JSON.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the catalog and report counts without writing to Sheets.",
    )
    parser.add_argument(
        "--delay-s",
        type=float,
        default=6.0,
        help="Delay between product upserts to reduce Google Sheets quota pressure.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        catalog = load_demo_catalog(args.catalog_path)
        storage = None if args.dry_run else make_sheets_storage(target=args.target)

        result = seed_demo_catalog_products(
            catalog=catalog,
            storage=storage,
            dry_run=args.dry_run,
            delay_s=args.delay_s,
        )

    except (RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    mode = "DRY RUN" if result.dry_run else "SEEDED"
    print(f"{mode}: {result.total_products} products loaded from catalog.")
    print(f"Target: {args.target}")

    if not result.dry_run:
        print(f"Upserted products: {result.upserted_products}")

    return 0


if __name__ == "__main__":
    sys.exit(main())