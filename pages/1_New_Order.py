from __future__ import annotations

from decimal import Decimal

import streamlit as st

from duna_orders.demo_catalog import DemoCatalogFile
from duna_orders.domain.models import DraftItemRequest, DraftOrderRequest, Product
from duna_orders.ui.setup import (
    get_demo_catalog,
    get_demo_messages,
    get_order_service,
    get_parsing_service,
    get_storage,
    prepare_storage_catalog,
)
from duna_orders.demo_messages import DemoMessagesFile
from duna_orders.domain.models import ParseResult
from duna_orders.parsing.prompts import PROMPT_VERSION
from duna_orders.services.parsing import ParsingService
from duna_orders.ui.parser_review import (
    DraftCandidate,
    parsed_result_to_draft_candidate,
)
from duna_orders.services.customer_context import (
    format_new_order_customer_context,
    get_customer_context_by_phone,
)
from duna_orders.ui.confirmation_message import generate_confirmation_message
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
    
    if "demo_messages" not in st.session_state:
        st.session_state.demo_messages = get_demo_messages()

    if "storage" not in st.session_state:
        storage = get_storage()
        st.session_state.catalog_ready = prepare_storage_catalog(
            storage,
            st.session_state.demo_catalog,
        )
        st.session_state.storage = storage

    if "order_service" not in st.session_state:
        st.session_state.order_service = get_order_service(st.session_state.storage)

    if "parsing_service" not in st.session_state:
        st.session_state.parsing_service = get_parsing_service(st.session_state.storage)

    if "draft_order_id" not in st.session_state:
        st.session_state.draft_order_id = None

    if "last_success_message" not in st.session_state:
        st.session_state.last_success_message = None
    if "last_confirmation_message" not in st.session_state:
        st.session_state.last_confirmation_message = None
    if "draft_candidate" not in st.session_state:
        st.session_state.draft_candidate = None

    if "selected_demo_message_id" not in st.session_state:
        st.session_state.selected_demo_message_id = None


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

@st.cache_data(show_spinner=False)
def _cached_parse_message(
    message_text: str,
    prompt_version: str,
) -> ParseResult:
    parsing_service: ParsingService | None = st.session_state.parsing_service
    catalog: DemoCatalogFile = st.session_state.demo_catalog
    storage: StorageInterface = st.session_state.storage

    if parsing_service is None:
        raise RuntimeError("Parser no disponible.")

    return parsing_service.parse(
        tenant_id=catalog.business.tenant_id,
        raw_message=message_text,
        products=storage.list_products(active_only=True),
    )


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

def _render_parsed_candidate(
    candidate: DraftCandidate,
    *,
    catalog: DemoCatalogFile,
    raw_message: str,
    order_service: OrderService,
    customer_name: str,
    customer_phone: str,
) -> None:
    st.divider()
    st.subheader("Revisión del parser")

    if candidate.warnings:
        for warning in candidate.warnings:
            st.warning(warning)

    if not candidate.items:
        st.info("No hay productos reconocidos para crear borrador desde el parser.")
        if st.button("Descartar y empezar de nuevo", key="discard_empty_candidate"):
            st.session_state.draft_candidate = None
            st.rerun()
        return

    selected_items: list[DraftItemRequest] = []

    for index, item in enumerate(candidate.items):
        product = item.matched_product
        product_label = product.product_name if product is not None else "no encontrado"

        with st.container(border=True):
            st.write(f"**Producto:** {product_label}")

            for warning in item.warnings:
                st.warning(warning)

            col_qty, col_mods = st.columns([1, 3])

            with col_qty:
                quantity = st.number_input(
                    "Cantidad",
                    min_value=0,
                    step=1,
                    value=int(item.quantity),
                    key=f"parsed_qty_{index}_{item.product_id}",
                )

            with col_mods:
                modifications = st.text_input(
                    "Modificaciones",
                    value=item.modifications or "",
                    key=f"parsed_mods_{index}_{item.product_id}",
                )

            if product is not None and quantity > 0:
                selected_items.append(
                    DraftItemRequest(
                        tenant_id=catalog.business.tenant_id,
                        product_id=product.product_id,
                        quantity=Decimal(str(quantity)),
                        modifications=modifications.strip() or None,
                    )
                )

    st.write("**Datos inferidos**")

    col_fulfillment, col_zone, col_payment = st.columns(3)

    with col_fulfillment:
        fulfillment_options = ["", "delivery", "pickup"]
        fulfillment_index = (
            fulfillment_options.index(candidate.inferred_fulfillment_type)
            if candidate.inferred_fulfillment_type in fulfillment_options
            else 0
        )
        parsed_fulfillment_type = st.selectbox(
            "Fulfillment inferido",
            options=fulfillment_options,
            index=fulfillment_index,
            format_func=lambda value: "Sin inferir" if value == "" else value,
            key="parsed_fulfillment_type",
        )

    with col_zone:
        parsed_delivery_zone = st.text_input(
            "Zona/dirección inferida",
            value=candidate.inferred_delivery_zone or "",
            key="parsed_delivery_zone",
        )

    with col_payment:
        payment_options = ["", "nequi", "daviplata", "transferencia", "efectivo"]
        payment_index = (
            payment_options.index(candidate.inferred_payment_method)
            if candidate.inferred_payment_method in payment_options
            else 0
        )
        parsed_payment_method = st.selectbox(
            "Pago inferido",
            options=payment_options,
            index=payment_index,
            format_func=lambda value: "Sin inferir" if value == "" else value,
            key="parsed_payment_method",
        )

    parsed_customer_notes = st.text_area(
        "Notas inferidas",
        value=candidate.inferred_customer_notes or "",
        height=80,
        key="parsed_customer_notes",
    )

    col_create, col_discard = st.columns(2)

    with col_create:
        can_create_parser_draft = bool(customer_name.strip()) and bool(selected_items)
        if st.button("Crear borrador con estos datos", disabled=not can_create_parser_draft):
            try:
                request = DraftOrderRequest(
                    tenant_id=catalog.business.tenant_id,
                    raw_message=raw_message.strip(),
                    customer_name=customer_name.strip(),
                    customer_phone=customer_phone.strip() or None,
                    fulfillment_type=parsed_fulfillment_type or None,
                    delivery_zone=parsed_delivery_zone.strip() or None,
                    packaging_fee=Decimal("0"),
                    customer_notes=parsed_customer_notes.strip() or None,
                    payment_method=parsed_payment_method or None,
                    items=selected_items,
                )
                order = order_service.create_draft(request)
                st.session_state.draft_order_id = order.order_id
                st.session_state.draft_candidate = None
                st.session_state.last_success_message = None
                st.session_state.last_confirmation_message = None
                st.rerun()
            except (
                EmptyDraftError,
                ProductNotFoundError,
                InactiveProductError,
                ValueError,
            ) as error:
                st.error(str(error))

    with col_discard:
        if st.button("Descartar y empezar de nuevo"):
            st.session_state.draft_candidate = None
            st.rerun()

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
            customer = (
                storage.get_customer(confirmed_order.customer_id)
                if confirmed_order.customer_id is not None
                else None
            )
            confirmation_message = generate_confirmation_message(
                confirmed_order,
                customer,
            )

            st.session_state.draft_order_id = None
            st.session_state.last_success_message = (
                f"Orden {confirmed_order.order_id} confirmada por {_money(confirmed_order.total)}."
            )
            st.session_state.last_confirmation_message = confirmation_message
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

if not st.session_state.catalog_ready:
    st.error("Catalog not seeded. Run scripts/seed_demo_catalog.py first.")
    st.stop()

catalog: DemoCatalogFile = st.session_state.demo_catalog
demo_messages: DemoMessagesFile = st.session_state.demo_messages


with st.sidebar:
    st.subheader("Demo")
    st.write(f"Backend: `{st.session_state.storage.__class__.__name__}`")
    st.write(f"Catálogo: `{catalog.business.business_name}`")
    st.caption(
        f"Tipo: {catalog.business.business_type} · "
        f"Moneda: {catalog.business.currency}"
    )

    if st.button("Reset UI session"):
        st.session_state.clear()
        st.rerun()

storage: StorageInterface = st.session_state.storage
order_service: OrderService = st.session_state.order_service
parsing_service: ParsingService | None = st.session_state.parsing_service

if st.session_state.last_success_message:
    st.success(st.session_state.last_success_message)

if st.session_state.last_confirmation_message:
    st.subheader("Mensaje para WhatsApp")
    st.code(st.session_state.last_confirmation_message, language="text")

products = storage.list_products(active_only=True)

st.subheader("Mensaje del cliente")

demo_message_options = [""] + [entry.id for entry in demo_messages.messages]
selected_demo_message_id = st.selectbox(
    "Cargar mensaje de demostración",
    options=demo_message_options,
    format_func=lambda value: "Selecciona un mensaje..." if value == "" else value,
    key="demo_message_selector",
)

if (
    selected_demo_message_id
    and selected_demo_message_id != st.session_state.selected_demo_message_id
):
    selected_entry = next(
        entry for entry in demo_messages.messages if entry.id == selected_demo_message_id
    )
    st.session_state.raw_message_input = selected_entry.message
    st.session_state.selected_demo_message_id = selected_demo_message_id
    st.session_state.draft_candidate = None
    st.rerun()

raw_message = st.text_area(
    "WhatsApp message",
    height=110,
    key="raw_message_input",
    placeholder=(
        "Buenas, me regala una bandeja paisa, una limonada de coco "
        "y una porción de aguacate. Pago por Nequi."
    ),
)

col_parse, col_parse_status = st.columns([1, 3])

with col_parse:
    parse_clicked = st.button(
        "Parsear mensaje",
        disabled=not bool(raw_message.strip()),
    )

with col_parse_status:
    if parsing_service is None:
        st.caption("Parser no disponible: configura ANTHROPIC_API_KEY para usarlo.")
    else:
        st.caption("Parser disponible.")

if parse_clicked:
    if parsing_service is None:
        st.warning(
            "Parser no disponible. Configura ANTHROPIC_API_KEY o completa la orden manualmente."
        )
    else:
        try:
            with st.spinner("Parseando mensaje..."):
                parse_result = _cached_parse_message(
                    raw_message.strip(),
                    PROMPT_VERSION,
                )
            st.session_state.draft_candidate = parsed_result_to_draft_candidate(
                parse_result,
                catalog,
                catalog.business.tenant_id,
            )
            st.rerun()
        except Exception as error:
            st.error(str(error))


col_customer, col_phone = st.columns(2)
with col_customer:
    customer_name = st.text_input("Customer name", placeholder="Carlos Pérez")
with col_phone:
    customer_phone = st.text_input("Customer phone", placeholder="3001234567")

if customer_phone.strip():
    customer_context = get_customer_context_by_phone(
        storage,
        tenant_id=catalog.business.tenant_id,
        phone=customer_phone,
    )
    customer_context_label = format_new_order_customer_context(customer_context)

    if customer_context.is_known_customer:
        st.success(customer_context_label)

        registered_name = customer_context.customer.customer_name
        typed_name = customer_name.strip()

        if typed_name and typed_name.casefold() != registered_name.casefold():
            st.caption(f"Se usará el nombre registrado: {registered_name}.")
    else:
        st.info(customer_context_label)
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

draft_candidate: DraftCandidate | None = st.session_state.draft_candidate

if draft_candidate is not None:
    _render_parsed_candidate(
        draft_candidate,
        catalog=catalog,
        raw_message=raw_message,
        order_service=order_service,
        customer_name=customer_name,
        customer_phone=customer_phone,
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
        st.session_state.last_confirmation_message = None
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