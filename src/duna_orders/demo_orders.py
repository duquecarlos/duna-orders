from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Sequence
from zoneinfo import ZoneInfo
from duna_orders.demo_ids import DEMO_ORDER_ID_PREFIX, DEMO_ORDER_ITEM_ID_PREFIX
from duna_orders.domain.models import (
    Customer,
    FulfillmentType,
    Order,
    OrderItem,
    OrderStatus,
    PaymentMethod,
    Product,
)


DEMO_TENANT_ID = "el-fogon-colombiano"
DEFAULT_DEMO_ORDER_COUNT = 1500
DEFAULT_DEMO_ANCHOR_DATE = date(2026, 5, 27)
DEMO_TIMEZONE = "America/Bogota"
REGULAR_CUSTOMER_COUNT = 30
MEDIUM_TAIL_CUSTOMER_COUNT = 100
ONE_TIME_CUSTOMER_COUNT = 600
LOW_FREQUENCY_ORDER_SHARE = 0.60
ONE_TIME_ORDER_SHARE = 0.40
LOCAL_ORDER_HOURS = (
    8,
    9,
    11,
    12,
    12,
    13,
    13,
    14,
    18,
    19,
    20,
)
WEEKDAY_DEMAND_WEIGHTS = {
    0: 0.75,  # Monday
    1: 0.80,  # Tuesday
    2: 0.95,  # Wednesday
    3: 1.05,  # Thursday
    4: 1.35,  # Friday
    5: 1.25,  # Saturday
    6: 1.55,  # Sunday
}

WEEK_DEMAND_MULTIPLIERS = (
    0.86,
    1.04,
    0.92,
    1.18,
    1.00,
)

DAILY_NOISE_RANGE = (0.72, 1.28)
DELIVERY_ZONES = (
    "Cedritos",
    "Contador",
    "Santa Bárbara",
    "Chicó Norte",
    "Colina Campestre",
    "Usaquén",
    "Alhambra",
    "Mazurén",
)

PAYMENT_METHODS: tuple[PaymentMethod, ...] = (
    "nequi",
    "daviplata",
    "transferencia",
    "efectivo",
)


@dataclass(frozen=True)
class DemoOrderDataset:
    orders: list[Order]
    order_items: list[OrderItem]


def build_demo_order_dataset(
    *,
    customers: Sequence[Customer],
    products: Sequence[Product],
    order_count: int = DEFAULT_DEMO_ORDER_COUNT,
    seed: int = 42,
    anchor_date: date = DEFAULT_DEMO_ANCHOR_DATE,
    tenant_id: str = DEMO_TENANT_ID,
) -> DemoOrderDataset:
    if order_count <= 0:
        raise ValueError("order_count must be greater than zero.")

    tenant_customers = [
        customer
        for customer in customers
        if customer.tenant_id == tenant_id
    ]
    tenant_products = [
        product
        for product in products
        if product.tenant_id == tenant_id and product.active
    ]

    if len(tenant_customers) < 2:
        raise ValueError("At least two demo customers are required.")

    if not tenant_products:
        raise ValueError("At least one active demo product is required.")

    rng = random.Random(seed)
    customer_plan = _build_customer_plan(
        rng=random.Random(seed + 1_000),
        customers=tenant_customers,
        order_count=order_count,
    )
    local_dates = _build_local_dates(
        rng=random.Random(seed + 2_000),
        order_count=order_count,
        anchor_date=anchor_date,
    )

    orders: list[Order] = []
    order_items: list[OrderItem] = []

    for index, local_date in enumerate(local_dates, start=1):
        created_at = _created_at_for_order(
            rng=rng,
            local_date=local_date,
        )
        customer = customer_plan[index - 1]
        status = _choose_status(
            rng=rng,
            local_date=local_date,
            anchor_date=anchor_date,
        )
        fulfillment_type = _choose_fulfillment_type(rng)
        payment_method = rng.choice(PAYMENT_METHODS)
        order_id = f"{DEMO_ORDER_ID_PREFIX}{index:05d}"

        items = _build_order_items(
            rng=rng,
            order_id=order_id,
            order_index=index,
            products=tenant_products,
            local_date=local_date,
            tenant_id=tenant_id,
        )
        subtotal = sum((item.line_total for item in items), Decimal("0"))
        delivery_fee = Decimal("5000") if fulfillment_type == "delivery" else Decimal("0")
        packaging_fee = Decimal("1000") if fulfillment_type == "delivery" else Decimal("0")
        total = subtotal + delivery_fee + packaging_fee

        confirmed_at = None if status == "draft" else created_at + timedelta(minutes=3)
        status_updated_at = _status_updated_at(
            created_at=created_at,
            status=status,
        )

        order = Order(
            tenant_id=tenant_id,
            order_id=order_id,
            created_at=created_at,
            updated_at=status_updated_at,
            customer_id=customer.customer_id,
            customer_name_snapshot=customer.customer_name,
            customer_phone_snapshot=customer.customer_phone,
            raw_message=f"Pedido demo {index:05d}",
            status=status,
            confirmed_at=confirmed_at,
            status_updated_at=status_updated_at,
            items=items,
            subtotal=subtotal,
            delivery_fee=delivery_fee,
            packaging_fee=packaging_fee,
            total=total,
            fulfillment_type=fulfillment_type,
            delivery_zone=(
                rng.choice(DELIVERY_ZONES)
                if fulfillment_type == "delivery"
                else None
            ),
            customer_notes=_customer_note(rng),
            payment_method=payment_method,
            delivery_date=local_date.isoformat(),
            delivery_address=(
                f"Calle {rng.randint(100, 170)} #{rng.randint(5, 80)}-{rng.randint(1, 99)}"
                if fulfillment_type == "delivery"
                else None
            ),
            notes="Demo order generated for dashboard validation.",
            confirmation_message=None,
            created_by="demo_seed",
        )

        orders.append(order)
        order_items.extend(items)

    return DemoOrderDataset(
        orders=orders,
        order_items=order_items,
    )


def _created_at_for_order(*, rng: random.Random, local_date: date) -> datetime:
    local_timezone = ZoneInfo(DEMO_TIMEZONE)
    local_hour = rng.choice(LOCAL_ORDER_HOURS)
    local_minute = rng.randrange(0, 60, 5)

    local_datetime = datetime.combine(
        local_date,
        time(local_hour, local_minute),
        tzinfo=local_timezone,
    )
    return local_datetime.astimezone(timezone.utc)

def _build_customer_plan(
    *,
    rng: random.Random,
    customers: Sequence[Customer],
    order_count: int,
) -> list[Customer]:
    required_customer_count = (
        REGULAR_CUSTOMER_COUNT
        + MEDIUM_TAIL_CUSTOMER_COUNT
        + ONE_TIME_CUSTOMER_COUNT
    )

    if len(customers) < required_customer_count:
        return _fallback_weighted_customer_plan(
            rng=rng,
            customers=customers,
            order_count=order_count,
        )

    regular_customers = list(customers[:REGULAR_CUSTOMER_COUNT])
    medium_tail_customers = list(
        customers[
            REGULAR_CUSTOMER_COUNT:
            REGULAR_CUSTOMER_COUNT + MEDIUM_TAIL_CUSTOMER_COUNT
        ]
    )
    one_time_customers = list(
        customers[
            REGULAR_CUSTOMER_COUNT + MEDIUM_TAIL_CUSTOMER_COUNT:
            required_customer_count
        ]
    )

    one_time_order_count = min(
        len(one_time_customers),
        int(order_count * ONE_TIME_ORDER_SHARE),
    )
    medium_tail_order_count = int(
        order_count * (LOW_FREQUENCY_ORDER_SHARE - ONE_TIME_ORDER_SHARE)
    )
    medium_customer_count = min(
        len(medium_tail_customers),
        max(0, medium_tail_order_count // 3),
    )

    plan: list[Customer] = []
    plan.extend(one_time_customers[:one_time_order_count])

    for customer in medium_tail_customers[:medium_customer_count]:
        plan.extend([customer, customer, customer])

    remaining_orders = order_count - len(plan)
    regular_weights = [
        9 if index < 8 else 3
        for index, _ in enumerate(regular_customers)
    ]

    plan.extend(
        rng.choices(
            regular_customers,
            weights=regular_weights,
            k=remaining_orders,
        )
    )

    rng.shuffle(plan)
    return plan


def _fallback_weighted_customer_plan(
    *,
    rng: random.Random,
    customers: Sequence[Customer],
    order_count: int,
) -> list[Customer]:
    weights = [
        8 if index < 8 else 3 if index < 16 else 1
        for index, _ in enumerate(customers)
    ]

    return rng.choices(
        list(customers),
        weights=weights,
        k=order_count,
    )


def _build_local_dates(
    *,
    rng: random.Random,
    order_count: int,
    anchor_date: date,
) -> list[date]:
    date_range = [
        anchor_date - timedelta(days=offset)
        for offset in range(34, -1, -1)
    ]
    raw_weights: list[float] = []

    for index, local_date in enumerate(date_range):
        week_index = index // 7
        weekday_weight = WEEKDAY_DEMAND_WEIGHTS[local_date.weekday()]
        week_multiplier = WEEK_DEMAND_MULTIPLIERS[week_index]
        noise = rng.uniform(*DAILY_NOISE_RANGE)
        raw_weights.append(weekday_weight * week_multiplier * noise)

    total_weight = sum(raw_weights)
    exact_counts = [
        order_count * weight / total_weight
        for weight in raw_weights
    ]
    counts = [int(value) for value in exact_counts]
    remainder = order_count - sum(counts)

    fractional_order = sorted(
        range(len(exact_counts)),
        key=lambda index: exact_counts[index] - counts[index],
        reverse=True,
    )

    for index in fractional_order[:remainder]:
        counts[index] += 1

    local_dates: list[date] = []
    for local_date, count in zip(date_range, counts):
        local_dates.extend([local_date] * count)

    return local_dates

def _choose_status(
    *,
    rng: random.Random,
    local_date: date,
    anchor_date: date,
) -> OrderStatus:
    if local_date == anchor_date:
        return rng.choices(
            ["draft", "confirmed", "in_preparation", "ready", "delivered", "cancelled"],
            weights=[8, 24, 20, 18, 24, 6],
            k=1,
        )[0]

    return rng.choices(
        ["delivered", "picked_up", "cancelled", "confirmed"],
        weights=[68, 12, 5, 15],
        k=1,
    )[0]


def _choose_fulfillment_type(rng: random.Random) -> FulfillmentType:
    return rng.choices(
        ["delivery", "pickup"],
        weights=[78, 22],
        k=1,
    )[0]


def _customer_note(rng: random.Random) -> str | None:
    notes = (
        None,
        None,
        None,
        "Sin cubiertos.",
        "Llamar al llegar.",
        "Dejar en portería.",
        "Poca salsa.",
        "Ají aparte.",
    )
    return rng.choice(notes)


def _build_order_items(
    *,
    rng: random.Random,
    order_id: str,
    order_index: int,
    products: Sequence[Product],
    local_date: date,
    tenant_id: str,
) -> list[OrderItem]:
    items: list[OrderItem] = []

    main_product = _choose_main_product(rng, products, local_date)
    items.append(
        _make_item(
            product=main_product,
            order_id=order_id,
            order_index=order_index,
            item_index=1,
            quantity=Decimal("1"),
            tenant_id=tenant_id,
        )
    )

    optional_groups = [
        ("bebida-", Decimal("1"), 0.78),
        ("acompanamiento-", Decimal("1"), 0.36),
        ("entrada-", Decimal("1"), 0.24),
        ("postre-", Decimal("1"), 0.16),
        ("adicion-", Decimal("1"), 0.20),
    ]

    item_index = 2
    used_product_ids = {main_product.product_id}

    for prefix, quantity, probability in optional_groups:
        if rng.random() > probability:
            continue

        candidates = [
            product
            for product in _products_by_prefix(products, prefix, local_date)
            if product.product_id not in used_product_ids
        ]

        if not candidates:
            continue

        product = rng.choice(candidates)
        used_product_ids.add(product.product_id)
        item_quantity = quantity

        if prefix == "bebida-" and rng.random() < 0.18:
            item_quantity = Decimal("2")

        items.append(
            _make_item(
                product=product,
                order_id=order_id,
                order_index=order_index,
                item_index=item_index,
                quantity=item_quantity,
                tenant_id=tenant_id,
            )
        )
        item_index += 1

    return items


def _choose_main_product(
    rng: random.Random,
    products: Sequence[Product],
    local_date: date,
) -> Product:
    mains = _products_by_prefix(
        products,
        ("plato-", "parrilla-", "sopa-"),
        local_date,
    )

    if not mains:
        raise ValueError("No active main products are available for this date.")

    weights = [_main_product_weight(product) for product in mains]
    return rng.choices(mains, weights=weights, k=1)[0]


def _main_product_weight(product: Product) -> int:
    product_id = product.product_id

    if product_id in {
        "plato-bandeja-paisa",
        "plato-pollo-guisado-criollo",
        "plato-frijoles-garra",
    }:
        return 8

    if product_id.startswith("parrilla-"):
        return 5

    if product_id.startswith("sopa-"):
        return 3

    if product_id in {
        "plato-cazuela-mariscos",
        "plato-arroz-con-camarones",
        "parrilla-picada-fogon-dos",
    }:
        return 2

    return 4


def _products_by_prefix(
    products: Sequence[Product],
    prefixes: str | tuple[str, ...],
    local_date: date,
) -> list[Product]:
    resolved_prefixes = (prefixes,) if isinstance(prefixes, str) else prefixes

    return [
        product
        for product in products
        if product.product_id.startswith(resolved_prefixes)
        and _product_available_on(product, local_date)
    ]


def _product_available_on(product: Product, local_date: date) -> bool:
    if product.available_days is None:
        return True

    weekday = local_date.strftime("%A").lower()
    return weekday in product.available_days


def _make_item(
    *,
    product: Product,
    order_id: str,
    order_index: int,
    item_index: int,
    quantity: Decimal,
    tenant_id: str,
) -> OrderItem:
    return OrderItem(
        tenant_id=tenant_id,
        order_item_id=f"{DEMO_ORDER_ITEM_ID_PREFIX}{order_index:05d}_{item_index:02d}",
        order_id=order_id,
        product_id=product.product_id,
        product_name_snapshot=product.product_name,
        unit_snapshot=product.unit,
        quantity=quantity,
        unit_price_snapshot=product.unit_price,
        line_total=product.unit_price * quantity,
        modifications=None,
        validation_status="ok",
        notes=None,
    )


def _status_updated_at(*, created_at: datetime, status: OrderStatus) -> datetime:
    if status == "draft":
        return created_at

    if status in {"confirmed", "in_preparation", "ready"}:
        return created_at + timedelta(minutes=20)

    if status in {"delivered", "picked_up"}:
        return created_at + timedelta(hours=1, minutes=15)

    if status == "cancelled":
        return created_at + timedelta(minutes=12)

    return created_at