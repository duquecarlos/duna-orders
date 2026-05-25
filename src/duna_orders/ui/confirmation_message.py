from __future__ import annotations

from decimal import Decimal

from duna_orders.domain.models import Customer, Order


def _money(value: Decimal) -> str:
    return f"${value:,.0f}".replace(",", ".")


def _customer_display_name(order: Order, customer: Customer | None) -> str:
    if customer is not None and customer.customer_name.strip():
        return customer.customer_name.strip()

    if order.customer_name_snapshot and order.customer_name_snapshot.strip():
        return order.customer_name_snapshot.strip()

    return "cliente"


def generate_confirmation_message(
    order: Order,
    customer: Customer | None = None,
) -> str:
    customer_name = _customer_display_name(order, customer)

    lines = [
        f"Hola {customer_name}! Confirmamos tu pedido:",
    ]

    for item in order.items:
        item_line = f"- {item.quantity:g}x {item.product_name_snapshot}"

        if item.modifications:
            item_line = f"{item_line} ({item.modifications})"

        lines.append(item_line)

    lines.append(f"Total: {_money(order.total)}")

    if order.payment_method:
        lines.append(f"Forma de pago: {order.payment_method.capitalize()}")

    if order.fulfillment_type == "delivery":
        destination = order.delivery_address or order.delivery_zone

        if destination:
            lines.append(f"Entrega a {destination} en aproximadamente 30-40 minutos.")
        else:
            lines.append("Entrega en aproximadamente 30-40 minutos.")

    elif order.fulfillment_type == "pickup":
        lines.append("Puedes recogerlo cuando te avisemos que está listo.")

    lines.append("Gracias por preferirnos! 🙏")

    return "\n".join(lines)