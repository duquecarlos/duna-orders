"""Seed deterministic demo customer data into a Google Sheets target.

Usage:
    python scripts/seed_demo_data.py --target demo --wipe --seed 42
    python scripts/seed_demo_data.py --target demo --seed 42
    python scripts/seed_demo_data.py --target runtime --seed 42

Manual smoke test:
    1. Set GOOGLE_SHEETS_DEMO_SPREADSHEET_ID in .env.
    2. Run:
       python scripts/seed_demo_data.py --target demo --wipe --seed 42
    3. Run the same command again.
    4. Confirm the customers tab contains the same deterministic demo customers

Safety:
    --target demo is the default.
    --wipe --target runtime is blocked.
    Demo target requires GOOGLE_SHEETS_DEMO_SPREADSHEET_ID before storage starts.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from typing import Literal, Protocol

from duna_orders.config import settings
from duna_orders.demo_customers import (
    DEMO_TENANT_ID,
    DEMO_TENANT_NAME,
    build_demo_customers,
)
from duna_orders.domain.models import Customer
from duna_orders.storage.schema import CUSTOMERS_TAB, TABS
from duna_orders.storage.sheets import GoogleSheetsStorage

Target = Literal["demo", "runtime"]


@dataclass(frozen=True)
class SeedResult:
    target: str
    tenant_id: str
    seed: int
    wipe: bool
    deleted_customers: int
    generated_customers: int
    seeded_customers: int


class CustomerStorage(Protocol):
    def create_customer(self, customer: Customer) -> Customer:
        pass

def _row_value(row: list[str], index: int) -> str:
    return row[index].strip() if index < len(row) else ""


def _contiguous_ranges(row_indexes: list[int]) -> list[tuple[int, int]]:
    if not row_indexes:
        return []

    ranges: list[tuple[int, int]] = []
    start = row_indexes[0]
    previous = row_indexes[0]

    for row_index in row_indexes[1:]:
        if row_index == previous + 1:
            previous = row_index
            continue

        ranges.append((start, previous))
        start = row_index
        previous = row_index

    ranges.append((start, previous))
    return ranges


def wipe_demo_customers(
    storage: GoogleSheetsStorage,
    *,
    tenant_id: str,
) -> int:
    worksheet = storage._worksheet(CUSTOMERS_TAB)
    values = storage._run_gspread(lambda: worksheet.get_all_values())

    headers = TABS[CUSTOMERS_TAB]
    tenant_id_col_index = headers.index("tenant_id")

    rows_to_delete = [
        row_index
        for row_index, row in enumerate(values[1:], start=2)
        if _row_value(row, tenant_id_col_index) == tenant_id
    ]

    for start, end in reversed(_contiguous_ranges(rows_to_delete)):
        storage._run_gspread(
            lambda start=start, end=end: worksheet.delete_rows(start, end)
        )

    if rows_to_delete:
        storage._invalidate_records_cache(CUSTOMERS_TAB)

    return len(rows_to_delete)


def seed_demo_customers(
    *,
    storage: CustomerStorage,
    customers: list[Customer],
    target: str,
    seed: int,
    wipe: bool,
    deleted_customers: int = 0,
    delay_s: float = 3.0,
) -> SeedResult:
    bulk_create_customers = getattr(storage, "bulk_create_customers", None)

    if callable(bulk_create_customers):
        bulk_create_customers(customers)
        seeded_customers = len(customers)
    else:
        seeded_customers = 0

        for customer in customers:
            storage.create_customer(customer)
            seeded_customers += 1

            if delay_s > 0:
                time.sleep(delay_s)

    return SeedResult(
        target=target,
        tenant_id=DEMO_TENANT_ID,
        seed=seed,
        wipe=wipe,
        deleted_customers=deleted_customers,
        generated_customers=len(customers),
        seeded_customers=seeded_customers,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed deterministic demo customers into Google Sheets."
    )
    parser.add_argument(
        "--target",
        choices=("demo", "runtime"),
        default="demo",
        help="Spreadsheet target. Defaults to demo.",
    )
    parser.add_argument(
        "--wipe",
        action="store_true",
        help="Delete existing demo-tenant customers before seeding.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Seed for deterministic background names and phone numbers.",
    )
    parser.add_argument(
        "--delay-s",
        type=float,
        default=3.0,
        help="Delay between customer inserts to reduce Google Sheets quota pressure.",
    )
    return parser.parse_args()


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


def main() -> int:
    args = parse_args()

    try:
        if args.target == "runtime" and args.wipe:
            raise RuntimeError("--wipe --target=runtime is blocked for safety.")

        storage = make_sheets_storage(target=args.target)
        customers = build_demo_customers(seed=args.seed)

        deleted_customers = 0
        if args.wipe:
            deleted_customers = wipe_demo_customers(
                storage,
                tenant_id=DEMO_TENANT_ID,
            )

        result = seed_demo_customers(
            storage=storage,
            customers=customers,
            target=args.target,
            seed=args.seed,
            wipe=args.wipe,
            deleted_customers=deleted_customers,
            delay_s=args.delay_s,
        )

    except (RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(f"SEEDED: {result.seeded_customers} customers.")
    print(f"Target: {result.target}")
    print(f"Tenant: {DEMO_TENANT_NAME} ({result.tenant_id})")
    print(f"Seed: {result.seed}")
    print(f"Wipe: {result.wipe}")
    print(f"Deleted existing customers: {result.deleted_customers}")

    return 0


if __name__ == "__main__":
    sys.exit(main())