from decimal import Decimal

from duna_orders.domain.models import Customer, Order, OrderItem
from duna_orders.ui.confirmation_message import generate_confirmation_message
from tests.conftest import DEFAULT_TEST_TENANT_ID


def make_order(
    *,
    fulfillment_type: str = "delivery",
    delivery_zone: str | None = "Chapinero",
    payment_method: str | None = "nequi",
) -> Order:
    return Order(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        order_id="ord_test",
        customer_name_snapshot="Andrea snapshot",
        raw_message="Pedido de prueba",
        items=[
            OrderItem(
                tenant_id=DEFAULT_TEST_TENANT_ID,
                order_item_id="oit_bandeja",
                order_id="ord_test",
                product_id="prd_bandeja",
                product_name_snapshot="Bandeja paisa",
                quantity=Decimal("2"),
                unit_price_snapshot=Decimal("35000"),
                line_total=Decimal("70000"),
                modifications="una sin chicharrón",
                validation_status="ok",
            ),
            OrderItem(
                tenant_id=DEFAULT_TEST_TENANT_ID,
                order_item_id="oit_limonada",
                order_id="ord_test",
                product_id="prd_limonada",
                product_name_snapshot="Limonada de coco",
                quantity=Decimal("1"),
                unit_price_snapshot=Decimal("12000"),
                line_total=Decimal("12000"),
                validation_status="ok",
            ),
        ],
        subtotal=Decimal("82000"),
        packaging_fee=Decimal("1000"),
        total=Decimal("83000"),
        fulfillment_type=fulfillment_type,
        delivery_zone=delivery_zone,
        payment_method=payment_method,
    )


def test_generate_confirmation_message_uses_customer_name_and_delivery_details():
    customer = Customer(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        customer_id="cus_andrea",
        customer_name="Andrea",
        customer_phone="3001234567",
    )

    message = generate_confirmation_message(make_order(), customer)

    assert message == (
        "Hola Andrea! Confirmamos tu pedido:\n"
        "- 2x Bandeja paisa (una sin chicharrón)\n"
        "- 1x Limonada de coco\n"
        "Total: $83.000\n"
        "Forma de pago: Nequi\n"
        "Entrega a Chapinero en aproximadamente 30-40 minutos.\n"
        "Gracias por preferirnos! 🙏"
    )


def test_generate_confirmation_message_falls_back_to_order_snapshot_name():
    message = generate_confirmation_message(make_order(), customer=None)

    assert message.startswith("Hola Andrea snapshot! Confirmamos tu pedido:")


def test_generate_confirmation_message_handles_pickup_orders():
    message = generate_confirmation_message(
        make_order(
            fulfillment_type="pickup",
            delivery_zone=None,
            payment_method=None,
        ),
        customer=None,
    )

    assert "Forma de pago:" not in message
    assert "Puedes recogerlo cuando te avisemos que está listo." in message