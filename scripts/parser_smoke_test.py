"""Run a fixed battery of WhatsApp-style messages through the parser.

Used for prompt iteration without the Streamlit UI.

Usage:
    python scripts/parser_smoke_test.py
"""

from decimal import Decimal

from duna_orders.domain.models import Product
from duna_orders.ids import new_id
from duna_orders.parsing.anthropic_parser import AnthropicParser
from duna_orders.parsing.exceptions import ParserError
from duna_orders.services.parsing import ParsingService
from duna_orders.storage.memory import InMemoryStorage


DEMO_PRODUCTS = [
    ("Pollo entero", "25000", 10),
    ("Gaseosa 1.5L", "6500", 30),
    ("Arroz 1kg", "4500", 50),
    ("Huevos x30", "18000", 15),
    ("Queso campesino 500g", "12000", 8),
]

MESSAGES = [
    "Buenas, me regala 2 pollos enteros y 3 gaseosas",
    "Hola, mándame 5 huevos x30 y un queso",
    "buenas tardes me hace el favor y me manda media docena de gaseosas",
    "necesito 1 kg de arroz y 2 pollos para hoy en la noche",
    "hola cómo va? oye tengamelo listo por favor: 1 pollo, 2 gaseosas, gracias!",
    "pollo y arroz",
    "Buenas. 3 pollos, 5 gaseosas grandes, 2 quesos. Gracias",
    "una panela y 3 pollos por fa",
    "necesito kilo y medio de queso",
    "pollo entero x 2, arroz por 3, huevos por 1",
    "asdfqwer xyz123",
    "",
]


def main() -> None:
    storage = InMemoryStorage()

    for name, price, stock in DEMO_PRODUCTS:
        storage.upsert_product(
            Product(
                product_id=new_id("prd"),
                product_name=name,
                unit_price=Decimal(price),
                current_stock=Decimal(str(stock)),
            )
        )

    parser = AnthropicParser()
    service = ParsingService(parser, storage)
    products = storage.list_products()

    for i, message in enumerate(MESSAGES, 1):
        print(f"\n--- Message {i} ---")
        print(f"INPUT: {message!r}")

        if not message.strip():
            print("(skipped: empty)")
            continue

        try:
            result = service.parse(message, products)

            if not result.request.items:
                print("  ITEMS: none")

            for item in result.request.items:
                product = storage.get_product(item.product_id)
                product_name = product.product_name if product else "<UNKNOWN>"
                print(f"  ITEM: {item.quantity} x {product_name} ({item.product_id})")

            if result.warnings:
                for warning in result.warnings:
                    print(f"  WARNING: {warning}")

            print(f"  latency_ms={result.latency_ms}")

        except ParserError as error:
            print(f"  ERROR ({type(error).__name__}): {error}")

    print(f"\nparse_log entries: {len(storage._parse_logs)}")


if __name__ == "__main__":
    main()