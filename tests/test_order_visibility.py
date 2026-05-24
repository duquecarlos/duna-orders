from datetime import date, datetime, timezone
from decimal import Decimal

from duna_orders.domain.models import Order
from duna_orders.services.order_visibility import filter_today_orders
from tests.conftest import DEFAULT_TEST_TENANT_ID


def make_order(
    *,
    order_id: str,
    tenant_id: str = DEFAULT_TEST_TENANT_ID,
    status: str = "confirmed",
    created_at: datetime,
) -> Order:
    return Order(
        tenant_id=tenant_id,
        order_id=order_id,
        created_at=created_at,
        raw_message="Pedido de prueba",
        status=status,
        subtotal=Decimal("10000"),
        total=Decimal("10000"),
    )


def test_filter_today_orders_keeps_active_orders_for_tenant_and_date():
    orders = [
        make_order(
            order_id="ord_today_confirmed",
            status="confirmed",
            created_at=datetime(2026, 5, 24, 14, 0, tzinfo=timezone.utc),
        ),
        make_order(
            order_id="ord_today_ready",
            status="ready",
            created_at=datetime(2026, 5, 24, 15, 0, tzinfo=timezone.utc),
        ),
        make_order(
            order_id="ord_other_tenant",
            tenant_id="other-tenant",
            status="confirmed",
            created_at=datetime(2026, 5, 24, 16, 0, tzinfo=timezone.utc),
        ),
        make_order(
            order_id="ord_yesterday",
            status="confirmed",
            created_at=datetime(2026, 5, 23, 14, 0, tzinfo=timezone.utc),
        ),
    ]

    visible_orders = filter_today_orders(
        orders,
        tenant_id=DEFAULT_TEST_TENANT_ID,
        target_date=date(2026, 5, 24),
        timezone_name="America/Bogota",
    )

    assert [order.order_id for order in visible_orders] == [
        "ord_today_ready",
        "ord_today_confirmed",
    ]


def test_filter_today_orders_excludes_completed_by_default():
    orders = [
        make_order(
            order_id="ord_confirmed",
            status="confirmed",
            created_at=datetime(2026, 5, 24, 14, 0, tzinfo=timezone.utc),
        ),
        make_order(
            order_id="ord_delivered",
            status="delivered",
            created_at=datetime(2026, 5, 24, 15, 0, tzinfo=timezone.utc),
        ),
        make_order(
            order_id="ord_picked_up",
            status="picked_up",
            created_at=datetime(2026, 5, 24, 16, 0, tzinfo=timezone.utc),
        ),
        make_order(
            order_id="ord_cancelled",
            status="cancelled",
            created_at=datetime(2026, 5, 24, 17, 0, tzinfo=timezone.utc),
        ),
    ]

    visible_orders = filter_today_orders(
        orders,
        tenant_id=DEFAULT_TEST_TENANT_ID,
        target_date=date(2026, 5, 24),
        timezone_name="America/Bogota",
    )

    assert [order.order_id for order in visible_orders] == ["ord_confirmed"]


def test_filter_today_orders_can_include_completed_orders():
    orders = [
        make_order(
            order_id="ord_confirmed",
            status="confirmed",
            created_at=datetime(2026, 5, 24, 14, 0, tzinfo=timezone.utc),
        ),
        make_order(
            order_id="ord_delivered",
            status="delivered",
            created_at=datetime(2026, 5, 24, 15, 0, tzinfo=timezone.utc),
        ),
    ]

    visible_orders = filter_today_orders(
        orders,
        tenant_id=DEFAULT_TEST_TENANT_ID,
        target_date=date(2026, 5, 24),
        timezone_name="America/Bogota",
        include_completed=True,
    )

    assert [order.order_id for order in visible_orders] == [
        "ord_delivered",
        "ord_confirmed",
    ]


def test_filter_today_orders_uses_local_timezone_date():
    orders = [
        make_order(
            order_id="ord_late_utc_previous_local_day",
            status="confirmed",
            created_at=datetime(2026, 5, 24, 2, 0, tzinfo=timezone.utc),
        ),
    ]

    visible_orders = filter_today_orders(
        orders,
        tenant_id=DEFAULT_TEST_TENANT_ID,
        target_date=date(2026, 5, 23),
        timezone_name="America/Bogota",
    )

    assert [order.order_id for order in visible_orders] == [
        "ord_late_utc_previous_local_day",
    ]