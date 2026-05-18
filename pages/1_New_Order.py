from decimal import Decimal

import streamlit as st

from duna_orders.domain.models import DraftItemRequest, DraftOrderRequest, Product
from duna_orders.ids import new_id
from duna_orders.services.exceptions import (
    EmptyDraftError,
    InactiveProductError,
    InsufficientStockError,
    InvalidOrderStateError,
    OrderNotFoundError,
    ProductNotFoundError,
)
from duna_orders.services.orders import OrderService
from duna_orders.storage.memory import InMemoryStorage


def _seed_demo_products(storage: InMemoryStorage) -> None:
    for name, price, stock in [
        ("Pollo entero", "25000", 10),
        ("Gaseosa 1.5L", "6500", 30),
        ("Arroz 1kg", "4500", 50),
        ("Huevos x30", "18000", 15),
        ("Queso campesino 500g", "12000", 8),
    ]:
        storage.upsert_product(
            Product(
                product_id=new_id("prd"),
                product_name=name,
                unit="unidad",
                unit_price=Decimal(price),
                current_stock=Decimal(str(stock)),
            )
        )


def _bootstrap_session() -> None:
    if "storage" not in st.session_state:
        st.session_state.storage = InMemoryStorage()
        _seed_demo_products(st.session_state.storage)

    if "order_service" not in st.session_state:
        st.session_state.order_service = OrderService(st.session_state.storage)

    if "draft_order_id" not in st.session_state:
        st.session_state.draft_order_id = None

    if "last_success_message" not in st.session_state:
        st.session_state.last_success_message = None


def _money(value: Decimal) -> str:
    return f"${value:,.0f}"


st.set_page_config(page_title="New Order", layout="wide")

st.title("📥 New Order")
st.caption(
    "Manual workflow — paste a WhatsApp message, build the order, "
    "review, confirm."
)

with st.sidebar:
    if st.button("Reset session"):
        st.session_state.clear()
        st.rerun()

_bootstrap_session()

storage: InMemoryStorage = st.session_state.storage
order_service: OrderService = st.session_state.order_service

if st.session_state.last_success_message:
    st.success(st.session_state.last_success_message)

st.subheader("Mensaje del cliente")

raw_message = st.text_area(
    "WhatsApp message",
    height=120,
    placeholder="Buenas, me regala 2 pollos y 3 gaseosas por favor",
)

customer_name = st.text_input("Customer name")

st.subheader("Productos")

products = storage.list_products()
selected_quantities: dict[str, int] = {}

for product in products:
    col_name, col_stock, col_qty = st.columns([3, 1, 1])

    with col_name:
        st.write(product.product_name)
        st.caption(_money(product.unit_price))

    with col_stock:
        st.write("Stock")
        st.write(product.current_stock)

    with col_qty:
        selected_quantities[product.product_id] = st.number_input(
            "Qty",
            min_value=0,
            step=1,
            value=0,
            key=f"qty_{product.product_id}",
            label_visibility="collapsed",
        )

has_selected_items = any(qty > 0 for qty in selected_quantities.values())
can_create_draft = bool(raw_message.strip()) and bool(customer_name.strip()) and has_selected_items

if st.button("Crear borrador", disabled=not can_create_draft):
    request = DraftOrderRequest(
        raw_message=raw_message.strip(),
        customer_name=customer_name.strip(),
        items=[
            DraftItemRequest(
                product_id=product_id,
                quantity=Decimal(str(qty)),
            )
            for product_id, qty in selected_quantities.items()
            if qty > 0
        ],
    )

    try:
        order = order_service.create_draft(request)
        st.session_state.draft_order_id = order.order_id
        st.session_state.last_success_message = None
        st.rerun()
    except (EmptyDraftError, ProductNotFoundError, InactiveProductError) as error:
        st.error(str(error))
if st.session_state.draft_order_id:
    st.divider()
    st.subheader("Borrador actual")

    draft_order = storage.get_order(st.session_state.draft_order_id)

    if draft_order is None:
        st.error("Draft order not found.")
        st.session_state.draft_order_id = None
    else:
        item_rows = [
            {
                "product": item.product_name_snapshot,
                "quantity": item.quantity,
                "unit_price": item.unit_price_snapshot,
                "line_total": item.line_total,
            }
            for item in draft_order.items
        ]

        st.dataframe(item_rows, use_container_width=True)
        st.write(f"Status: `{draft_order.status}`")
        st.write(f"Total: **{_money(draft_order.total)}**")

        if st.button("Confirmar orden", type="primary"):
            try:
                confirmed_order = order_service.confirm_order(draft_order.order_id)
                st.session_state.draft_order_id = None
                st.session_state.last_success_message = (
                    f"Orden {confirmed_order.order_id} confirmada."
                )
                st.rerun()
            except (
                InsufficientStockError,
                ProductNotFoundError,
                InvalidOrderStateError,
                OrderNotFoundError,
            ) as error:
                st.error(str(error))

st.divider()
st.subheader("Inventario actual")

inventory_rows = [
    {
        "product_name": product.product_name,
        "current_stock": product.current_stock,
    }
    for product in storage.list_products(active_only=False)
]

st.dataframe(inventory_rows, use_container_width=True)

st.subheader("Movimientos de stock")

movement_rows = [
    {
        "created_at": movement.created_at,
        "product_id": movement.product_id,
        "quantity_delta": movement.quantity_delta,
        "reason": movement.reason,
        "reference_id": movement.reference_id,
    }
    for movement in storage.list_stock_movements()
]

movement_rows = sorted(
    movement_rows,
    key=lambda row: row["created_at"],
    reverse=True,
)

st.dataframe(movement_rows, use_container_width=True)