from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

from duna_orders.domain.models import Order


ACTIVE_ORDER_STATUSES = {
    "draft",
    "approved",
    "confirmed",
    "in_preparation",
    "ready",
}

COMPLETED_ORDER_STATUSES = {
    "delivered",
    "picked_up",
    "cancelled",
}


def order_created_on_local_date(
    order: Order,
    target_date: date,
    timezone_name: str,
) -> bool:
    local_created_at = order.created_at.astimezone(ZoneInfo(timezone_name))
    return local_created_at.date() == target_date


def filter_today_orders(
    orders: list[Order],
    *,
    tenant_id: str,
    target_date: date,
    timezone_name: str,
    include_completed: bool = False,
) -> list[Order]:
    visible_orders = []

    for order in orders:
        if order.tenant_id != tenant_id:
            continue

        if not order_created_on_local_date(order, target_date, timezone_name):
            continue

        if not include_completed and order.status in COMPLETED_ORDER_STATUSES:
            continue

        visible_orders.append(order)

    return sorted(
        visible_orders,
        key=lambda order: order.created_at,
        reverse=True,
    )
