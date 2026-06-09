from __future__ import annotations

from decimal import Decimal

from duna_orders.domain.models import Order


def generate_order_confirmed_acknowledgement(
    order: Order,
    *,
    business_name: str | None = None,
) -> str:
    customer_name = _customer_display_name(order)
    lines = [
        f"Hola {customer_name}, tu pedido quedó confirmado.",
        "",
        "Pedido:",
    ]

    lines.extend(_item_line(item.quantity, item.product_name_snapshot, item.modifications) for item in order.items)
    lines.extend(
        [
            "",
            f"Total: {_format_cop(order.total)}",
        ]
    )

    if order.payment_method:
        lines.append(f"Pago: {_format_payment_method(order.payment_method)}")

    fulfillment_line = _fulfillment_line(order)
    if fulfillment_line:
        lines.append(fulfillment_line)

    lines.extend(
        [
            "",
            f"Gracias por pedir en {_business_display_name(business_name)}.",
        ]
    )

    return "\n".join(lines)


def _customer_display_name(order: Order) -> str:
    if order.customer_name_snapshot and order.customer_name_snapshot.strip():
        return order.customer_name_snapshot.strip()

    return "cliente"


def _item_line(
    quantity: Decimal,
    product_name: str,
    modifications: str | None,
) -> str:
    line = f"- {quantity:g}x {product_name}"

    if modifications and modifications.strip():
        line = f"{line} ({modifications.strip()})"

    return line


def _format_cop(value: Decimal) -> str:
    return f"${value:,.0f}".replace(",", ".")


def _format_payment_method(payment_method: str) -> str:
    return payment_method.capitalize()


def _fulfillment_line(order: Order) -> str | None:
    destination = order.delivery_address or order.delivery_zone

    if order.fulfillment_type == "delivery":
        if destination:
            return f"Entrega: {destination}"

        return "Entrega: domicilio"

    if order.fulfillment_type == "pickup":
        if destination:
            return f"Recogida: {destination}"

        return "Recogida en tienda"

    return None


def _business_display_name(business_name: str | None) -> str:
    if business_name and business_name.strip():
        return business_name.strip()

    return "nuestro negocio"
