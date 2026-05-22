"""Seed the demo restaurant catalog products into Google Sheets.

Usage:
    python scripts/seed_demo_catalog.py --dry-run
    python scripts/seed_demo_catalog.py
"""

from __future__ import annotations
import time
import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from duna_orders.demo_catalog import DemoCatalogFile, load_demo_catalog
from duna_orders.domain.models import Product
from duna_orders.storage.sheets import GoogleSheetsStorage


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

def make_test_sheets_storage() -> GoogleSheetsStorage:
    spreadsheet_id = os.getenv("GOOGLE_SHEETS_TEST_SPREADSHEET_ID")
    credentials_path = os.getenv(
        "GOOGLE_SHEETS_CREDENTIALS_PATH",
        "./credentials/service_account.json",
    )

    if not spreadsheet_id:
        raise RuntimeError("GOOGLE_SHEETS_TEST_SPREADSHEET_ID is not set.")

    production_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")
    if production_id and production_id == spreadsheet_id:
        raise RuntimeError(
            "GOOGLE_SHEETS_TEST_SPREADSHEET_ID must not equal "
            "GOOGLE_SHEETS_SPREADSHEET_ID."
        )

    return GoogleSheetsStorage(
        spreadsheet_id=spreadsheet_id,
        credentials_path=credentials_path,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed demo catalog products into the test Google Sheet."
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
        default=1.5,
        help="Delay between product upserts to reduce Google Sheets quota pressure.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    catalog = load_demo_catalog(args.catalog_path)

    storage = None if args.dry_run else make_test_sheets_storage()

    result = seed_demo_catalog_products(
        catalog=catalog,
        storage=storage,
        dry_run=args.dry_run,
        delay_s=args.delay_s,
    )

    mode = "DRY RUN" if result.dry_run else "SEEDED"
    print(f"{mode}: {result.total_products} products loaded from catalog.")

    if not result.dry_run:
        print(f"Upserted products: {result.upserted_products}")

    return 0


if __name__ == "__main__":
    sys.exit(main())