from __future__ import annotations

from decimal import Decimal

from duna_orders.domain.models import Order, OrderItem
from duna_orders.services.acknowledgement_template import (
    generate_order_confirmed_acknowledgement,
)
from tests.conftest import DEFAULT_TEST_TENANT_ID


def test_acknowledgement_renders_full_confirmed_delivery_order() -> None:
    message = generate_order_confirmed_acknowledgement(
        _make_order(
            customer_name_snapshot="Carlos",
            fulfillment_type="delivery",
            delivery_zone="Chapinero",
            payment_method="nequi",
            total=Decimal("58000"),
            items=[
                _make_item(
                    quantity=Decimal("1"),
                    product_name_snapshot="Bandeja paisa",
                    modifications="sin aguacate",
                ),
                _make_item(
                    quantity=Decimal("2"),
                    product_name_snapshot="Limonada natural",
                ),
            ],
        ),
        business_name="El Fogón Colombiano",
    )

    assert message == (
        "Hola Carlos, tu pedido quedó confirmado.\n"
        "\n"
        "Pedido:\n"
        "- 1x Bandeja paisa (sin aguacate)\n"
        "- 2x Limonada natural\n"
        "\n"
        "Total: $58.000\n"
        "Pago: Nequi\n"
        "Entrega: Chapinero\n"
        "\n"
        "Gracias por pedir en El Fogón Colombiano."
    )


def test_acknowledgement_uses_customer_name_fallback() -> None:
    message = generate_order_confirmed_acknowledgement(
        _make_order(customer_name_snapshot="  "),
    )

    assert message.startswith("Hola cliente, tu pedido quedó confirmado.")


def test_acknowledgement_omits_missing_payment_method() -> None:
    message = generate_order_confirmed_acknowledgement(
        _make_order(payment_method=None),
    )

    assert "\nPago:" not in message


def test_acknowledgement_handles_delivery_without_destination() -> None:
    message = generate_order_confirmed_acknowledgement(
        _make_order(
            fulfillment_type="delivery",
            delivery_zone=None,
            delivery_address=None,
        ),
    )

    assert "Entrega: domicilio" in message
    assert "minutos" not in message


def test_acknowledgement_prefers_delivery_address_over_zone() -> None:
    message = generate_order_confirmed_acknowledgement(
        _make_order(
            fulfillment_type="delivery",
            delivery_zone="Chapinero",
            delivery_address="Calle 1 # 2-3",
        ),
    )

    assert "Entrega: Calle 1 # 2-3" in message
    assert "Chapinero" not in message


def test_acknowledgement_renders_pickup_fulfillment() -> None:
    message = generate_order_confirmed_acknowledgement(
        _make_order(
            fulfillment_type="pickup",
            delivery_zone=None,
            delivery_address=None,
        ),
    )

    assert "Recogida en tienda" in message
    assert "avisemos" not in message


def test_acknowledgement_uses_business_name_fallback() -> None:
    message = generate_order_confirmed_acknowledgement(
        _make_order(),
        business_name=" ",
    )

    assert message.endswith("Gracias por pedir en nuestro negocio.")


def test_acknowledgement_formats_cop_without_decimals() -> None:
    message = generate_order_confirmed_acknowledgement(
        _make_order(total=Decimal("1234567.89")),
    )

    assert "Total: $1.234.568" in message


def _make_order(
    *,
    customer_name_snapshot: str | None = "Carlos",
    fulfillment_type: str | None = "delivery",
    delivery_zone: str | None = "Chapinero",
    delivery_address: str | None = None,
    payment_method: str | None = "nequi",
    total: Decimal = Decimal("58000"),
    items: list[OrderItem] | None = None,
) -> Order:
    return Order(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        order_id="ord_ack",
        customer_name_snapshot=customer_name_snapshot,
        customer_phone_snapshot="+573001112233",
        raw_message="Pedido por WhatsApp",
        status="confirmed",
        items=items
        or [
            _make_item(
                quantity=Decimal("1"),
                product_name_snapshot="Bandeja paisa",
            )
        ],
        total=total,
        fulfillment_type=fulfillment_type,
        delivery_zone=delivery_zone,
        delivery_address=delivery_address,
        payment_method=payment_method,
    )


def _make_item(
    *,
    quantity: Decimal,
    product_name_snapshot: str,
    modifications: str | None = None,
) -> OrderItem:
    return OrderItem(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        order_item_id=f"oit_{product_name_snapshot}",
        order_id="ord_ack",
        product_name_snapshot=product_name_snapshot,
        quantity=quantity,
        unit_price_snapshot=Decimal("1000"),
        line_total=quantity * Decimal("1000"),
        modifications=modifications,
        validation_status="ok",
    )
