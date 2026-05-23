from __future__ import annotations

from decimal import Decimal

import streamlit as st

from duna_orders.demo_catalog import DemoCatalogFile
from duna_orders.domain.models import DraftItemRequest, DraftOrderRequest, Product
from duna_orders.ui.setup import (
    get_demo_catalog,
    get_order_service,
    get_parsing_service,
    get_storage,
    seed_inmemory_from_catalog,
)

from duna_orders.services.exceptions import (
    EmptyDraftError,
    InactiveProductError,
    InsufficientStockError,
    InvalidOrderStateError,
    OrderNotFoundError,
    ProductNotFoundError,
)
from duna_orders.services.orders import OrderService
from duna_orders.storage.base import StorageInterface
from duna_orders.storage.memory import InMemoryStorage


CATEGORY_LABELS = {
    "entradas": "Entradas",
    "sopas": "Sopas",
    "platos_fuertes": "Platos fuertes",
    "parrilla": "Parrilla",
    "acompañamientos": "Acompañamientos",
    "bebidas": "Bebidas",
    "postres": "Postres",
    "adiciones": "Adiciones",
}

CATEGORY_ORDER = list(CATEGORY_LABELS.keys())


def _money(value: Decimal) -> str:
    return f"${value:,.0f}".replace(",", ".")


def _bootstrap_session() -> None:
    if "demo_catalog" not in st.session_state:
        st.session_state.demo_catalog = get_demo_catalog()

    if "storage" not in st.session_state:
        storage = get_storage()
        if isinstance(storage, InMemoryStorage):
            seed_inmemory_from_catalog(storage, st.session_state.demo_catalog)
        st.session_state.storage = storage

    if "order_service" not in st.session_state:
        st.session_state.order_service = get_order_service(st.session_state.storage)

    if "parsing_service" not in st.session_state:
        st.session_state.parsing_service = get_parsing_service(st.session_state.storage)

    if "draft_order_id" not in st.session_state:
        st.session_state.draft_order_id = None

    if "last_success_message" not in st.session_state:
        st.session_state.last_success_message = None


def _products_by_category(products: list[Product]) -> dict[str, list[Product]]:
    grouped: dict[str, list[Product]] = {category: [] for category in CATEGORY_ORDER}

    for product in products:
        category = product.category or "otros"
        grouped.setdefault(category, []).append(product)

    return grouped


def _parse_decimal_input(value: str, *, default: Decimal = Decimal("0")) -> Decimal:
    clean = value.strip().replace(".", "").replace(",", ".")
    if not clean:
        return default
    return Decimal(clean)


def _render_product_selector(products: list[Product]) -> dict[str, dict[str, object]]:
    selected: dict[str, dict[str, object]] = {}
    grouped = _products_by_category(products)

    for category in CATEGORY_ORDER:
        category_products = grouped.get(category, [])
        if not category_products:
            continue

        with st.expander(CATEGORY_LABELS[category], expanded=category in {"platos_fuertes", "bebidas"}):
            for product in category_products:
                col_info, col_stock, col_qty, col_mods = st.columns([4, 1, 1, 3])

                with col_info:
                    st.write(f"**{product.product_name}**")
                    st.caption(f"{_money(product.unit_price)} · {product.unit}")

                with col_stock:
                    st.caption("Stock")
                    st.write(product.current_stock)

                with col_qty:
                    qty = st.number_input(
                        "Cantidad",
                        min_value=0,
                        step=1,
                        value=0,
                        key=f"qty_{product.product_id}",
                        label_visibility="collapsed",
                    )

                with col_mods:
                    modifications = st.text_input(
                        "Modificaciones",
                        placeholder="sin cebolla, aparte...",
                        key=f"mods_{product.product_id}",
                        label_visibility="collapsed",
                    )

                if qty > 0:
                    selected[product.product_id] = {
                        "quantity": Decimal(str(qty)),
                        "modifications": modifications.strip() or None,
                    }

    return selected


def _render_draft(
            order_id: str,
            storage: StorageInterface,
            order_service: OrderService,
        ) -> None:
    draft_order = storage.get_order(order_id)

    if draft_order is None:
        st.error("Draft order not found.")
        st.session_state.draft_order_id = None
        return

    st.subheader("Borrador actual")

    left, right = st.columns([2, 1])

    with left:
        st.write(f"**Cliente:** {draft_order.customer_name_snapshot or 'Sin nombre'}")
        if draft_order.customer_phone_snapshot:
            st.write(f"**Teléfono:** {draft_order.customer_phone_snapshot}")
        if draft_order.fulfillment_type:
            st.write(f"**Entrega:** `{draft_order.fulfillment_type}`")
        if draft_order.delivery_zone:
            st.write(f"**Zona:** {draft_order.delivery_zone}")
        if draft_order.payment_method:
            st.write(f"**Pago:** `{draft_order.payment_method}`")
        if draft_order.customer_notes:
            st.info(draft_order.customer_notes)

    with right:
        st.metric("Subtotal", _money(draft_order.subtotal))
        st.metric("Empaque", _money(draft_order.packaging_fee))
        st.metric("Total", _money(draft_order.total))

    item_rows = [
        {
            "Producto": item.product_name_snapshot,
            "Cantidad": item.quantity,
            "Precio unitario": _money(item.unit_price_snapshot),
            "Total línea": _money(item.line_total),
            "Modificaciones": item.modifications or "",
        }
        for item in draft_order.items
    ]

    st.dataframe(item_rows, use_container_width=True, hide_index=True)

    if st.button("Confirmar orden", type="primary"):
        try:
            confirmed_order = order_service.confirm_order(draft_order.order_id)
            st.session_state.draft_order_id = None
            st.session_state.last_success_message = (
                f"Orden {confirmed_order.order_id} confirmada por {_money(confirmed_order.total)}."
            )
            st.rerun()
        except (
            InsufficientStockError,
            ProductNotFoundError,
            InvalidOrderStateError,
            OrderNotFoundError,
        ) as error:
            st.error(str(error))


st.set_page_config(page_title="Nueva orden", page_icon="🧾", layout="wide")

st.title("🧾 Nueva orden")
st.caption("Demo local con catálogo colombiano real — revisar borrador antes de confirmar.")

_bootstrap_session()

catalog: DemoCatalogFile = st.session_state.demo_catalog

with st.sidebar:
    st.subheader("Demo")
    st.write("Backend: `InMemoryStorage`")
    st.write(f"Catálogo: `{catalog.business.business_name}`")
    st.caption(
        f"Tipo: {catalog.business.business_type} · "
        f"Moneda: {catalog.business.currency}"
    )

    if st.button("Reset session"):
        st.session_state.clear()
        st.rerun()

storage: StorageInterface = st.session_state.storage
order_service: OrderService = st.session_state.order_service

if st.session_state.last_success_message:
    st.success(st.session_state.last_success_message)

products = storage.list_products(active_only=True)

st.subheader("Mensaje del cliente")

raw_message = st.text_area(
    "WhatsApp message",
    height=110,
    placeholder=(
        "Buenas, me regala una bandeja paisa, una limonada de coco "
        "y una porción de aguacate. Pago por Nequi."
    ),
)

col_customer, col_phone = st.columns(2)
with col_customer:
    customer_name = st.text_input("Customer name", placeholder="Carlos Pérez")
with col_phone:
    customer_phone = st.text_input("Customer phone", placeholder="3001234567")

col_fulfillment, col_zone, col_payment, col_packaging = st.columns(4)

with col_fulfillment:
    fulfillment_type = st.selectbox(
        "Fulfillment",
        options=["delivery", "pickup", "dine_in"],
        index=0,
    )

with col_zone:
    delivery_zone = st.text_input("Delivery zone", placeholder="Chapinero")

with col_payment:
    payment_method = st.selectbox(
        "Payment",
        options=["cash", "nequi", "daviplata", "card", "transfer"],
        index=1,
    )

with col_packaging:
    packaging_fee_text = st.text_input("Packaging fee", value="1000")

customer_notes = st.text_area(
    "Customer notes",
    height=80,
    placeholder="Sin cubiertos, dejar en portería, salsa aparte...",
)

st.divider()
st.subheader("Productos")

selected_items = _render_product_selector(products)
has_selected_items = bool(selected_items)

can_create_draft = bool(raw_message.strip()) and bool(customer_name.strip()) and has_selected_items

if st.button("Crear borrador", disabled=not can_create_draft):
    try:
        request = DraftOrderRequest(
            tenant_id=catalog.business.tenant_id,
            raw_message=raw_message.strip(),
            customer_name=customer_name.strip(),
            customer_phone=customer_phone.strip() or None,
            fulfillment_type=fulfillment_type,
            delivery_zone=delivery_zone.strip() or None,
            packaging_fee=_parse_decimal_input(packaging_fee_text),
            customer_notes=customer_notes.strip() or None,
            payment_method=payment_method,
            items=[
                DraftItemRequest(
                    tenant_id=catalog.business.tenant_id,
                    product_id=product_id,
                    quantity=item_data["quantity"],
                    modifications=item_data["modifications"],
                )
                for product_id, item_data in selected_items.items()
            ],
        )

        order = order_service.create_draft(request)
        st.session_state.draft_order_id = order.order_id
        st.session_state.last_success_message = None
        st.rerun()
    except (EmptyDraftError, ProductNotFoundError, InactiveProductError, ValueError) as error:
        st.error(str(error))

if st.session_state.draft_order_id:
    st.divider()
    _render_draft(st.session_state.draft_order_id, storage, order_service)

st.divider()
st.subheader("Inventario actual")

inventory_rows = [
    {
        "Producto": product.product_name,
        "Categoría": product.category,
        "Stock": product.current_stock,
        "Precio": _money(product.unit_price),
        "Stock mínimo": product.min_stock,
    }
    for product in storage.list_products(active_only=False)
]

st.dataframe(inventory_rows, use_container_width=True, hide_index=True)