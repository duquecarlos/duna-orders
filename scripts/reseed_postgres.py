from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from duna_orders.config import settings  # noqa: E402
from duna_orders.demo_dataset import generate_demo_dataset  # noqa: E402
from duna_orders.storage.postgres import PostgresStorage  # noqa: E402
from duna_orders.storage.postgres_session import (  # noqa: E402
    make_engine,
    make_session_factory,
)


def main() -> int:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required to reseed Postgres demo data.")

    engine = make_engine(settings.database_url)
    storage = PostgresStorage(make_session_factory(engine))

    try:
        dataset = generate_demo_dataset()
        counts = storage.reseed_demo_dataset(dataset)
    finally:
        engine.dispose()

    print("Postgres demo reseed complete.")
    print(f"tenant_id: {dataset.tenant_id}")
    print(f"products: {counts['products']}")
    print(f"customers: {counts['customers']}")
    print(f"orders: {counts['orders']}")
    print(f"order_items: {counts['order_items']}")
    print(f"stock_movements: {counts['stock_movements']}")
    print(f"parse_log: {counts['parse_log']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())