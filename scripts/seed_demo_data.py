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
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Protocol

from duna_orders.config import settings
from duna_orders.domain.models import Customer
from duna_orders.storage.schema import CUSTOMERS_TAB, TABS
from duna_orders.storage.sheets import GoogleSheetsStorage


Target = Literal["demo", "runtime"]

DEMO_TENANT_ID = "el-fogon-colombiano"
DEMO_TENANT_NAME = "El Fogón Colombiano"
NAMED_REGULAR_COUNT = 8
BACKGROUND_REGULAR_COUNT = 22
REGULAR_CUSTOMER_COUNT = NAMED_REGULAR_COUNT + BACKGROUND_REGULAR_COUNT
MEDIUM_TAIL_CUSTOMER_COUNT = 100
ONE_TIME_CUSTOMER_COUNT = 600
DEMO_CUSTOMER_COUNT = (
    REGULAR_CUSTOMER_COUNT
    + MEDIUM_TAIL_CUSTOMER_COUNT
    + ONE_TIME_CUSTOMER_COUNT
)
DEMO_CUSTOMER_ID_PREFIX = "demo_cus_"
DEMO_CREATED_AT = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

COLOMBIAN_MOBILE_PREFIXES = (
    300,
    301,
    302,
    304,
    305,
    310,
    311,
    312,
    313,
    314,
    315,
    316,
    317,
    318,
    320,
    321,
    322,
    323,
)


@dataclass(frozen=True)
class NamedRegular:
    customer_name: str
    backstory: str


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


NAMED_REGULARS = (
    NamedRegular(
        "Marta Restrepo",
        "Jubilada del barrio, pide bandeja paisa los martes.",
    ),
    NamedRegular(
        "Luis Fernando Rojas",
        "Taxista de confianza, suele pedir almuerzo para llevar.",
    ),
    NamedRegular(
        "Gloria Patricia Cárdenas",
        "Profesora cercana, pide sopas y platos suaves entre semana.",
    ),
    NamedRegular(
        "Andrés Felipe Salazar",
        "Trabaja en oficina cercana, pide ejecutivo con limonada.",
    ),
    NamedRegular(
        "Claudia Marcela Torres",
        "Compra para su familia los domingos después de misa.",
    ),
    NamedRegular(
        "Jorge Enrique Medina",
        "Cliente de finca, pide picadas grandes cuando viene a Bogotá.",
    ),
    NamedRegular(
        "Diana Carolina Vargas",
        "Vecina del conjunto, pide por WhatsApp y paga con Nequi.",
    ),
    NamedRegular(
        "Óscar Iván Gutiérrez",
        "Hincha del equipo del barrio, pide combos para ver partidos.",
    ),
)

BACKGROUND_FIRST_NAMES = (
    "Camila",
    "Santiago",
    "Valentina",
    "Juan Pablo",
    "Daniela",
    "Felipe",
    "Natalia",
    "Sebastián",
    "Laura",
    "Mateo",
    "Paula",
    "Nicolás",
    "Catalina",
    "Alejandro",
    "Manuela",
    "David",
    "Mariana",
    "Esteban",
    "Carolina",
    "Ricardo",
    "Tatiana",
    "Mauricio",
    "Juliana",
    "Cristian",
    "Adriana",
    "Diego",
)

BACKGROUND_LAST_NAMES = (
    "Gómez",
    "Moreno",
    "Castro",
    "Ramírez",
    "Herrera",
    "Suárez",
    "Pardo",
    "Mejía",
    "Cortés",
    "Acosta",
    "Vega",
    "Rincón",
    "Barrera",
    "Arias",
    "Quintero",
    "Beltrán",
    "Cifuentes",
    "Camacho",
)
TAIL_FIRST_NAMES = (
    "Ana María",
    "Carlos Andrés",
    "María Camila",
    "Juan Sebastián",
    "Laura Marcela",
    "José David",
    "Valentina",
    "Sergio Andrés",
    "Paola Andrea",
    "Miguel Ángel",
    "Daniela",
    "Julián",
    "Natalia",
    "Felipe",
    "Carolina",
    "Andrés",
    "Diana Marcela",
    "Juan Camilo",
    "Luisa Fernanda",
    "Cristian David",
    "Mónica",
    "Ricardo",
    "Patricia",
    "Alejandra",
    "Óscar Mauricio",
    "Sandra Milena",
    "Camilo",
    "Viviana",
    "Hernán",
    "Claudia Patricia",
)

TAIL_LAST_NAMES = (
    "García",
    "Rodríguez",
    "Martínez",
    "López",
    "González",
    "Pérez",
    "Sánchez",
    "Ramírez",
    "Torres",
    "Flores",
    "Rivera",
    "Gómez",
    "Díaz",
    "Reyes",
    "Morales",
    "Ortiz",
    "Vargas",
    "Castro",
    "Jiménez",
    "Rojas",
    "Moreno",
    "Muñoz",
    "Álvarez",
    "Romero",
    "Suárez",
    "Herrera",
    "Medina",
    "Cortés",
    "Arias",
    "Cárdenas",
)


def _customer_id(index: int) -> str:
    return f"{DEMO_CUSTOMER_ID_PREFIX}{index:03d}"


def _generate_phone_numbers(*, seed: int, count: int) -> list[str]:
    rng = random.Random(seed)
    numbers: set[str] = set()

    while len(numbers) < count:
        prefix = rng.choice(COLOMBIAN_MOBILE_PREFIXES)
        middle = rng.randint(100, 999)
        last = rng.randint(1000, 9999)
        numbers.add(f"+57 {prefix} {middle:03d} {last:04d}")

    return sorted(numbers)


def _background_customer_names(*, seed: int, count: int) -> list[str]:
    rng = random.Random(seed + 10_000)
    names = [
        f"{first_name} {last_name}"
        for first_name in BACKGROUND_FIRST_NAMES
        for last_name in BACKGROUND_LAST_NAMES
    ]
    rng.shuffle(names)
    return names[:count]

def _tail_customer_names(*, seed: int, count: int) -> list[str]:
    rng = random.Random(seed + 20_000)
    names: list[str] = []
    seen: set[str] = set()

    while len(names) < count:
        first_name = rng.choice(TAIL_FIRST_NAMES)
        first_last_name = rng.choice(TAIL_LAST_NAMES)
        second_last_name = rng.choice(TAIL_LAST_NAMES)

        if first_last_name == second_last_name:
            continue

        name = f"{first_name} {first_last_name} {second_last_name}"

        if name in seen:
            continue

        seen.add(name)
        names.append(name)

    return names

def build_demo_customers(
    *,
    seed: int,
    tenant_id: str = DEMO_TENANT_ID,
) -> list[Customer]:
    if len(NAMED_REGULARS) != NAMED_REGULAR_COUNT:
        raise RuntimeError(
            f"Expected {NAMED_REGULAR_COUNT} named regulars, "
            f"got {len(NAMED_REGULARS)}."
        )

    phones = _generate_phone_numbers(seed=seed, count=DEMO_CUSTOMER_COUNT)
    customers: list[Customer] = []

    for index, regular in enumerate(NAMED_REGULARS, start=1):
        customers.append(
            Customer(
                tenant_id=tenant_id,
                customer_id=_customer_id(index),
                customer_name=regular.customer_name,
                customer_phone=phones[index - 1],
                notes=regular.backstory,
                created_at=DEMO_CREATED_AT,
                updated_at=DEMO_CREATED_AT,
                last_order_at=None,
            )
        )

    background_names = _background_customer_names(
        seed=seed,
        count=BACKGROUND_REGULAR_COUNT,
    )
    for offset, customer_name in enumerate(
        background_names,
        start=len(customers) + 1,
    ):
        customers.append(
            Customer(
                tenant_id=tenant_id,
                customer_id=_customer_id(offset),
                customer_name=customer_name,
                customer_phone=phones[offset - 1],
                notes=None,
                created_at=DEMO_CREATED_AT,
                updated_at=DEMO_CREATED_AT,
                last_order_at=None,
            )
        )
    tail_names = _tail_customer_names(
        seed=seed,
        count=MEDIUM_TAIL_CUSTOMER_COUNT + ONE_TIME_CUSTOMER_COUNT,
    )

    for customer_name in tail_names:
        offset = len(customers) + 1
        customers.append(
            Customer(
                tenant_id=tenant_id,
                customer_id=_customer_id(offset),
                customer_name=customer_name,
                customer_phone=phones[offset - 1],
                notes="Cliente ocasional de demo.",
                created_at=DEMO_CREATED_AT,
                updated_at=DEMO_CREATED_AT,
                last_order_at=None,
            )
        )
    _validate_demo_customers(customers)
    return customers


def _validate_demo_customers(customers: list[Customer]) -> None:
    if len(customers) != DEMO_CUSTOMER_COUNT:
        raise RuntimeError(
            f"Expected {DEMO_CUSTOMER_COUNT} customers, got {len(customers)}."
        )

    customer_ids = [customer.customer_id for customer in customers]
    phones = [customer.customer_phone for customer in customers]

    if len(set(customer_ids)) != len(customer_ids):
        raise RuntimeError("Generated duplicate customer IDs.")

    if len(set(phones)) != len(phones):
        raise RuntimeError("Generated duplicate customer phone numbers.")

    invalid_phones = [
        phone
        for phone in phones
        if phone is None or not phone.startswith("+57 3")
    ]
    if invalid_phones:
        raise RuntimeError(f"Generated invalid Colombian phones: {invalid_phones}")


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