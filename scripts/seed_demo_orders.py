"""Seed a limited batch of deterministic demo orders into the demo spreadsheet.

Usage:
    python scripts/seed_demo_orders.py --target demo --wipe --limit 25 --delay-s 6
    python scripts/seed_demo_orders.py --target demo --limit 25 --delay-s 6

Safety:
    - Only demo target is allowed in this slice.
    - Runtime target is blocked.
    - --wipe deletes only rows with demo order/order-item prefixes.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from duna_orders.config import settings
from duna_orders.demo_catalog import load_demo_catalog
from duna_orders.demo_orders import (
    DEFAULT_DEMO_ORDER_COUNT,
    DEMO_TENANT_ID,
    build_demo_order_dataset,
)
from duna_orders.domain.models import Order
from duna_orders.storage.schema import ORDER_ITEMS_TAB, ORDERS_TAB, TABS
from duna_orders.storage.sheets import GoogleSheetsStorage
from scripts.seed_demo_data import build_demo_customers


DEMO_ORDER_ID_PREFIX = "demo_ord_"
DEMO_ORDER_ITEM_ID_PREFIX = "demo_oit_"


@dataclass(frozen=True)
class OrderSeedResult:
    target: str
    limit: int
    seed: int
    wipe: bool
    deleted_orders: int
    deleted_order_items: int
    seeded_orders: int
    seeded_order_items: int


def _resolve_demo_spreadsheet_id() -> str:
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


def make_demo_storage() -> GoogleSheetsStorage:
    return GoogleSheetsStorage(
        spreadsheet_id=_resolve_demo_spreadsheet_id(),
        credentials_path=str(settings.google_sheets_credentials_path),
    )


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


def _delete_prefixed_rows(
    storage: GoogleSheetsStorage,
    *,
    tab_name: str,
    id_column: str,
    id_prefix: str,
) -> int:
    worksheet = storage._worksheet(tab_name)
    values = storage._run_gspread(lambda: worksheet.get_all_values())

    headers = TABS[tab_name]
    id_col_index = headers.index(id_column)
    tenant_id_col_index = headers.index("tenant_id")

    rows_to_delete = [
        row_index
        for row_index, row in enumerate(values[1:], start=2)
        if _row_value(row, tenant_id_col_index) == DEMO_TENANT_ID
        and _row_value(row, id_col_index).startswith(id_prefix)
    ]

    for start, end in reversed(_contiguous_ranges(rows_to_delete)):
        storage._run_gspread(
            lambda start=start, end=end: worksheet.delete_rows(start, end)
        )

    if rows_to_delete:
        storage._invalidate_records_cache(tab_name)

    return len(rows_to_delete)


def wipe_demo_orders(storage: GoogleSheetsStorage) -> tuple[int, int]:
    deleted_order_items = _delete_prefixed_rows(
        storage,
        tab_name=ORDER_ITEMS_TAB,
        id_column="order_item_id",
        id_prefix=DEMO_ORDER_ITEM_ID_PREFIX,
    )
    deleted_orders = _delete_prefixed_rows(
        storage,
        tab_name=ORDERS_TAB,
        id_column="order_id",
        id_prefix=DEMO_ORDER_ID_PREFIX,
    )

    return deleted_orders, deleted_order_items


def build_limited_demo_orders(
    *,
    limit: int,
    seed: int,
) -> list[Order]:
    if limit <= 0:
        raise ValueError("--limit must be greater than zero.")

    if limit > DEFAULT_DEMO_ORDER_COUNT:
        raise ValueError(
            f"--limit must be <= {DEFAULT_DEMO_ORDER_COUNT} for this slice."
        )

    customers = build_demo_customers(seed=seed)
    catalog = load_demo_catalog()
    dataset = build_demo_order_dataset(
        customers=customers,
        products=catalog.products,
        order_count=limit,
        seed=seed,
    )

    return dataset.orders


def seed_demo_orders(
    *,
    storage: GoogleSheetsStorage,
    orders: list[Order],
    delay_s: float,
) -> tuple[int, int]:
    seeded_orders = 0
    seeded_order_items = 0

    for order in orders:
        storage.create_order(order)
        seeded_orders += 1
        seeded_order_items += len(order.items)

        if delay_s > 0:
            time.sleep(delay_s)

    return seeded_orders, seeded_order_items


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed a limited deterministic demo order batch into Sheets."
    )
    parser.add_argument(
        "--target",
        choices=("demo", "runtime"),
        default="demo",
        help="Spreadsheet target. Only demo is allowed in this slice.",
    )
    parser.add_argument(
        "--wipe",
        action="store_true",
        help="Delete existing demo orders/order_items before seeding.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Number of demo orders to write. Defaults to 25.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Seed for deterministic demo order generation.",
    )
    parser.add_argument(
        "--delay-s",
        type=float,
        default=6.0,
        help="Delay between order writes to reduce Google Sheets quota pressure.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        if args.target != "demo":
            raise RuntimeError("--target=runtime is blocked for demo order seeding.")

        storage = make_demo_storage()
        orders = build_limited_demo_orders(
            limit=args.limit,
            seed=args.seed,
        )

        deleted_orders = 0
        deleted_order_items = 0
        if args.wipe:
            deleted_orders, deleted_order_items = wipe_demo_orders(storage)

        seeded_orders, seeded_order_items = seed_demo_orders(
            storage=storage,
            orders=orders,
            delay_s=args.delay_s,
        )

        result = OrderSeedResult(
            target=args.target,
            limit=args.limit,
            seed=args.seed,
            wipe=args.wipe,
            deleted_orders=deleted_orders,
            deleted_order_items=deleted_order_items,
            seeded_orders=seeded_orders,
            seeded_order_items=seeded_order_items,
        )

    except (RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(f"SEEDED: {result.seeded_orders} orders.")
    print(f"Target: {result.target}")
    print(f"Tenant: {DEMO_TENANT_ID}")
    print(f"Limit: {result.limit}")
    print(f"Seed: {result.seed}")
    print(f"Wipe: {result.wipe}")
    print(f"Deleted existing orders: {result.deleted_orders}")
    print(f"Deleted existing order items: {result.deleted_order_items}")
    print(f"Seeded order items: {result.seeded_order_items}")

    return 0


if __name__ == "__main__":
    sys.exit(main())