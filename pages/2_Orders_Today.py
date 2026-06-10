from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import streamlit as st

from duna_orders.config import settings
from duna_orders.demo_catalog import DemoCatalogFile
from duna_orders.domain.models import Order
from duna_orders.services.exceptions import InvalidOrderTransitionError, OrderNotFoundError
from duna_orders.services.order_visibility import filter_today_orders
from duna_orders.services.orders import OrderService, get_allowed_next_statuses
from duna_orders.services.tenant_scoped_reads import TenantScopedReadService
from duna_orders.storage.base import StorageInterface
from duna_orders.storage.outbound_messages import ORDER_CONFIRMED_ACK
from duna_orders.storage.read_context import sheets_request_context
from duna_orders.ui.outbound_acknowledgement import (
    map_acknowledgement_status_to_ui_state,
    map_acknowledgement_unavailable_reason_to_ui_message,
)
from duna_orders.ui.setup import (
    OutboundAcknowledgementServiceSetup,
    get_demo_catalog,
    get_order_service,
    get_outbound_acknowledgement_service,
    get_storage,
)
from duna_orders.services.customer_context import (
    format_today_order_customer_badge,
    get_customer_context_by_phone,
)


STATUS_LABELS = {
    "draft": "Draft",
    "confirmed": "Confirmed",
    "in_preparation": "In preparation",
    "ready": "Ready",
    "delivered": "Delivered",
    "picked_up": "Picked up",
    "cancelled": "Cancelled",
}

ACTION_LABELS = {
    "in_preparation": "Start preparation",
    "ready": "Mark ready",
    "delivered": "Mark delivered",
    "picked_up": "Mark picked up",
    "cancelled": "Cancel",
}


def _money(value: Decimal) -> str:
    return f"${value:,.0f}".replace(",", ".")


def _short_id(order_id: str) -> str:
    return order_id[-8:] if len(order_id) > 8 else order_id


def _bootstrap_session() -> None:
    if "demo_catalog" not in st.session_state:
        st.session_state.demo_catalog = get_demo_catalog()

    if "storage" not in st.session_state:
        st.session_state.storage = get_storage()

    if "order_service" not in st.session_state:
        st.session_state.order_service = get_order_service(st.session_state.storage)

    if "outbound_acknowledgement_setup" not in st.session_state:
        st.session_state.outbound_acknowledgement_setup = (
            get_outbound_acknowledgement_service(st.session_state.storage)
        )


def _format_local_datetime(value: datetime) -> str:
    timezone = ZoneInfo(settings.default_timezone)
    return value.astimezone(timezone).strftime("%Y-%m-%d %H:%M")

def _render_outbound_acknowledgement_action(
    order: Order,
    *,
    setup: OutboundAcknowledgementServiceSetup,
    business_name: str,
) -> None:
    st.write("Acknowledgement")

    if not setup.is_available:
        st.info(
            map_acknowledgement_unavailable_reason_to_ui_message(
                setup.unavailable_reason
            )
        )
        return

    if (
        setup.service is None
        or setup.tenant_id is None
        or setup.from_number is None
        or setup.acknowledgement_store is None
    ):
        st.warning("Outbound acknowledgement is not fully configured.")
        return

    acknowledgement = setup.acknowledgement_store.get_for_order_acknowledgement(
        tenant_id=setup.tenant_id,
        order_id=order.order_id,
        acknowledgement_type=ORDER_CONFIRMED_ACK,
    )
    status_state = map_acknowledgement_status_to_ui_state(
        acknowledgement,
        has_required_order_details=bool(order.customer_phone_snapshot),
    )

    if status_state.show_send_button and st.button(
        "Send acknowledgement",
        key=f"{order.order_id}_send_acknowledgement",
    ):
        setup.service.send_order_confirmed_acknowledgement(
            tenant_id=setup.tenant_id,
            order_id=order.order_id,
            from_number=setup.from_number,
            requested_by="operator",
            business_name=business_name,
        )
        st.rerun()

    st.info(status_state.message)


def _render_order_card(
    order: Order,
    *,
    storage: StorageInterface,
    order_service: OrderService,
    tenant_id: str,
    business_name: str,
    outbound_acknowledgement_setup: OutboundAcknowledgementServiceSetup,
) -> None:
    with st.container(border=True):
        top_left, top_right = st.columns([3, 1])

        with top_left:
            st.write(
                f"**{order.customer_name_snapshot or 'Sin nombre'}** "
                f"`{_short_id(order.order_id)}`"
            )
            st.caption(
                f"Created: {_format_local_datetime(order.created_at)} · "
                f"Fulfillment: {order.fulfillment_type or 'not set'}"
            )

        with top_right:
            st.metric("Total", _money(order.total))

        status_label = STATUS_LABELS.get(order.status, order.status)
        st.write(f"Status: `{status_label}`")

        customer_context = get_customer_context_by_phone(
            storage,
            tenant_id=tenant_id,
            phone=order.customer_phone_snapshot,
        )
        st.caption(format_today_order_customer_badge(customer_context))

        if order.customer_phone_snapshot:
            st.write(f"Phone: {order.customer_phone_snapshot}")

        if order.delivery_zone:
            st.write(f"Zone/address: {order.delivery_zone}")

        if order.customer_notes:
            st.info(order.customer_notes)

        item_rows = [
            {
                "Product": item.product_name_snapshot,
                "Qty": item.quantity,
                "Line total": _money(item.line_total),
                "Mods": item.modifications or "",
            }
            for item in order.items
        ]

        st.dataframe(item_rows, use_container_width=True, hide_index=True)

        if order.status == "confirmed":
            _render_outbound_acknowledgement_action(
                order,
                setup=outbound_acknowledgement_setup,
                business_name=business_name,
            )

        allowed_statuses = get_allowed_next_statuses(order)

        if not allowed_statuses:
            st.caption("No further actions available.")
            return

        cols = st.columns(len(allowed_statuses))

        for col, next_status in zip(cols, allowed_statuses):
            with col:
                label = ACTION_LABELS.get(next_status, f"Move to {next_status}")

                if st.button(label, key=f"{order.order_id}_{next_status}"):
                    try:
                        updated_order = order_service.transition_order_status(
                            order.order_id,
                            tenant_id,
                            next_status,
                        )
                        st.success(
                            f"Order {_short_id(updated_order.order_id)} updated to "
                            f"{STATUS_LABELS.get(updated_order.status, updated_order.status)}."
                        )
                        st.rerun()
                    except (InvalidOrderTransitionError, OrderNotFoundError) as error:
                        st.error(str(error))


st.set_page_config(page_title="Today's orders", page_icon="📋", layout="wide")

st.title("📋 Today's orders")
st.caption("Active order visibility and simple lifecycle management.")

_bootstrap_session()

catalog: DemoCatalogFile = st.session_state.demo_catalog
storage: StorageInterface = st.session_state.storage
order_service: OrderService = st.session_state.order_service
outbound_acknowledgement_setup: OutboundAcknowledgementServiceSetup = (
    st.session_state.outbound_acknowledgement_setup
)

tenant_id = catalog.business.tenant_id
timezone = ZoneInfo(settings.default_timezone)
today = datetime.now(timezone).date()

with sheets_request_context(storage):
    with st.sidebar:
        st.subheader("Demo")
        st.write(f"Backend: `{storage.__class__.__name__}`")
        st.write(f"Business: `{catalog.business.business_name}`")
        st.write(f"Date: `{today.isoformat()}`")
        include_completed = st.toggle("Include completed/cancelled", value=False)

        if st.button("Refresh"):
            st.rerun()

    scoped_reads = TenantScopedReadService(storage)
    orders = filter_today_orders(
        scoped_reads.list_orders(tenant_id=tenant_id),
        tenant_id=tenant_id,
        target_date=today,
        timezone_name=settings.default_timezone,
        include_completed=include_completed,
    )

    if not orders:
        st.info("No orders found for today with the current filters.")
        st.stop()

    summary_rows = [
        {
            "Order": _short_id(order.order_id),
            "Customer": order.customer_name_snapshot or "Sin nombre",
            "Total": _money(order.total),
            "Status": STATUS_LABELS.get(order.status, order.status),
            "Fulfillment": order.fulfillment_type or "",
            "Created": _format_local_datetime(order.created_at),
        }
        for order in orders
    ]

    st.dataframe(summary_rows, use_container_width=True, hide_index=True)

    st.divider()

    for order in orders:
        _render_order_card(
            order,
            storage=storage,
            order_service=order_service,
            tenant_id=tenant_id,
            business_name=catalog.business.business_name,
            outbound_acknowledgement_setup=outbound_acknowledgement_setup,
        )
