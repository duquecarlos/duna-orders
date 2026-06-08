from __future__ import annotations

from decimal import Decimal

import streamlit as st

from duna_orders.demo_catalog import DemoCatalogFile
from duna_orders.domain.models import Order
from duna_orders.services.exceptions import InvalidOrderTransitionError, OrderNotFoundError
from duna_orders.services.inbound_draft_review import (
    InboundDraftReviewItem,
    InboundDraftReviewService,
)
from duna_orders.services.orders import OrderService
from duna_orders.storage.base import StorageInterface
from duna_orders.storage.read_context import sheets_request_context
from duna_orders.ui.setup import (
    get_demo_catalog,
    get_inbound_draft_review_service,
    get_order_service,
    get_storage,
)
from duna_orders.utils.money import format_cop


def _bootstrap_session() -> None:
    if "demo_catalog" not in st.session_state:
        st.session_state.demo_catalog = get_demo_catalog()

    if "storage" not in st.session_state:
        st.session_state.storage = get_storage()

    if "order_service" not in st.session_state:
        st.session_state.order_service = get_order_service(st.session_state.storage)

    if "inbound_draft_review_service" not in st.session_state:
        st.session_state.inbound_draft_review_service = (
            get_inbound_draft_review_service(st.session_state.storage)
        )

    if "inbound_review_success_message" not in st.session_state:
        st.session_state.inbound_review_success_message = None


def _short_id(order_id: str) -> str:
    return order_id[-8:] if len(order_id) > 8 else order_id


def _display_value(value: object) -> str:
    if value is None or value == "":
        return "Not set"

    return str(value)


def _item_rows(order: Order) -> list[dict[str, object]]:
    return [
        {
            "Item": item.product_name_snapshot,
            "Qty": item.quantity,
            "Modifiers": item.modifications or "",
            "Line total": format_cop(item.line_total),
        }
        for item in order.items
    ]


def _has_zero_total(order: Order) -> bool:
    return order.total == Decimal("0")


def _review_draft(
    *,
    order_service: OrderService,
    order: Order,
    tenant_id: str,
    decision: str,
) -> None:
    try:
        updated_order = order_service.review_inbound_draft(
            order_id=order.order_id,
            tenant_id=tenant_id,
            decision=decision,
        )
        st.session_state.inbound_review_success_message = (
            f"Order {_short_id(updated_order.order_id)} "
            f"{'approved' if decision == 'approve' else 'rejected'}."
        )
        st.rerun()
    except (InvalidOrderTransitionError, OrderNotFoundError, ValueError) as error:
        st.error(f"Could not review order {_short_id(order.order_id)}: {error}")


def _render_review_item(
    item: InboundDraftReviewItem,
    *,
    order_service: OrderService,
    tenant_id: str,
) -> None:
    order = item.order

    with st.container(border=True):
        header_left, header_right = st.columns([3, 1])

        with header_left:
            st.subheader(f"Order {_short_id(order.order_id)}")
            st.caption(f"Message `{item.message_sid}`")

        with header_right:
            st.metric("Total", format_cop(order.total))

        if not order.items:
            st.warning("Suspicious draft: no parsed items.")

        if _has_zero_total(order):
            st.warning("Suspicious draft: total is zero.")

        raw_col, parsed_col = st.columns(2)

        with raw_col:
            st.write("**Raw inbound message**")
            if item.from_number:
                st.caption(f"From: {item.from_number}")
            st.code(item.raw_inbound_body, language="text")

        with parsed_col:
            st.write("**Parsed draft**")
            st.write(f"Customer: {_display_value(order.customer_name_snapshot)}")
            st.write(f"Phone: {_display_value(order.customer_phone_snapshot)}")
            st.write(f"Fulfillment: `{_display_value(order.fulfillment_type)}`")
            st.write(f"Payment: `{_display_value(order.payment_method)}`")
            st.write(f"Delivery zone: {_display_value(order.delivery_zone)}")
            st.write(f"Delivery address: {_display_value(order.delivery_address)}")

            if order.customer_notes:
                st.info(order.customer_notes)

            item_rows = _item_rows(order)

            if item_rows:
                st.dataframe(item_rows, use_container_width=True, hide_index=True)
            else:
                st.write("No parsed items.")

        action_approve, action_reject = st.columns(2)

        with action_approve:
            if st.button(
                "Approve draft",
                key=f"approve_{order.order_id}_{item.message_sid}",
                type="primary",
            ):
                _review_draft(
                    order_service=order_service,
                    order=order,
                    tenant_id=tenant_id,
                    decision="approve",
                )

        with action_reject:
            if st.button(
                "Reject draft",
                key=f"reject_{order.order_id}_{item.message_sid}",
            ):
                _review_draft(
                    order_service=order_service,
                    order=order,
                    tenant_id=tenant_id,
                    decision="reject",
                )


st.set_page_config(page_title="Inbound review", page_icon="IR", layout="wide")

st.title("Inbound review")
st.caption("Operator review for inbound-created draft orders.")

_bootstrap_session()

catalog: DemoCatalogFile = st.session_state.demo_catalog
storage: StorageInterface = st.session_state.storage
order_service: OrderService = st.session_state.order_service
review_service: InboundDraftReviewService | None = (
    st.session_state.inbound_draft_review_service
)
tenant_id = catalog.business.tenant_id

with sheets_request_context(storage):
    with st.sidebar:
        st.subheader("Demo")
        st.write(f"Backend: `{storage.__class__.__name__}`")
        st.write(f"Business: `{catalog.business.business_name}`")
        st.write(f"Tenant: `{tenant_id}`")

        if st.button("Refresh"):
            st.rerun()

    if review_service is None:
        st.info("Inbound draft review is available only with the Postgres backend.")
        st.stop()

    if st.session_state.inbound_review_success_message:
        st.success(st.session_state.inbound_review_success_message)
        st.session_state.inbound_review_success_message = None

    try:
        review_items = review_service.list_reviewable_inbound_drafts(
            tenant_id=tenant_id,
        )
    except Exception as error:
        st.error(f"Could not load inbound drafts: {error}")
        st.stop()

    if not review_items:
        st.info("No inbound-created draft orders are waiting for review.")
        st.stop()

    for review_item in review_items:
        _render_review_item(
            review_item,
            order_service=order_service,
            tenant_id=tenant_id,
        )
