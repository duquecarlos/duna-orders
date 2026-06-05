"""Bulk seed deterministic demo orders into the demo spreadsheet.

Usage:
    python scripts/seed_demo_orders.py --target demo --wipe --limit 100
    python scripts/seed_demo_orders.py --target demo --wipe --limit 1500

Safety:
    - Only demo target is allowed in this slice.
    - Runtime target is blocked.
    - --wipe deletes only rows where tenant_id is el-fogon-colombiano and
      order_id starts with the demo order prefix.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from duna_orders.config import settings  # noqa: E402
from duna_orders.demo_catalog import load_demo_catalog  # noqa: E402
from duna_orders.demo_customers import build_demo_customers  # noqa: E402
from duna_orders.demo_ids import DEMO_ORDER_ID_PREFIX  # noqa: E402
from duna_orders.demo_orders import (  # noqa: E402
    DEFAULT_DEMO_ORDER_COUNT,
    DEMO_TENANT_ID,
    build_demo_order_dataset,
)
from duna_orders.domain.models import Order  # noqa: E402
from duna_orders.storage.sheets import GoogleSheetsStorage  # noqa: E402


@dataclass(frozen=True)
class OrderSeedResult:
    target: str
    limit: int
    seed: int
    wipe: bool
    deleted_rows: int
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


def seed_demo_orders_bulk(
    *,
    storage: GoogleSheetsStorage,
    orders: list[Order],
) -> tuple[int, int]:
    order_items = [item for order in orders for item in order.items]

    storage.bulk_create_order_items(order_items)
    storage.bulk_create_orders(orders)

    return len(orders), len(order_items)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bulk seed deterministic demo orders into Sheets."
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
        default=100,
        help="Number of demo orders to write. Defaults to 100.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Seed for deterministic demo order generation.",
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

        deleted_rows = 0
        if args.wipe:
            deleted_rows = storage.bulk_delete_orders_by_id_prefix(
                tenant_id=DEMO_TENANT_ID,
                prefix=DEMO_ORDER_ID_PREFIX,
            )

        seeded_orders, seeded_order_items = seed_demo_orders_bulk(
            storage=storage,
            orders=orders,
        )

        result = OrderSeedResult(
            target=args.target,
            limit=args.limit,
            seed=args.seed,
            wipe=args.wipe,
            deleted_rows=deleted_rows,
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
    print(f"Deleted existing order/order_item rows: {result.deleted_rows}")
    print(f"Seeded order items: {result.seeded_order_items}")

    return 0


if __name__ == "__main__":
    sys.exit(main())