from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timezone

from duna_orders.domain.models import Customer


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