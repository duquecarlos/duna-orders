"""Smoke test GoogleSheetsStorage against a real test spreadsheet.

Usage:
    python scripts/smoke_google_sheets.py
"""

import os
import sys
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import gspread

from duna_orders.domain.models import (
    Customer,
    Order,
    OrderItem,
    ParseLogEntry,
    Product,
    StockMovement,
)
from duna_orders.storage.schema import TABS
from duna_orders.storage.sheets import GoogleSheetsStorage


PRIMARY_ID_COLUMNS = {
    "products": "product_id",
    "customers": "customer_id",
    "orders": "order_id",
    "order_items": "order_item_id",
    "stock_movements": "stock_movement_id",
    "parse_log": "parse_id",
}
def _sleep_between_steps() -> None:
    delay_s = float(os.getenv("LIVE_SHEETS_TEST_DELAY_S", "8"))
    time.sleep(delay_s)

def main() -> int:
    run_token = f"smoke_{uuid.uuid4().hex[:8]}_"
    storage = _make_storage()

    checks: list[tuple[str, bool, str]] = []

    try:
        checks.append(_run("bootstrap validates headers", lambda: _check_bootstrap(storage)))
        _sleep_between_steps()

        checks.append(_run("create product", lambda: _check_product(storage, run_token)))
        _sleep_between_steps()

        checks.append(_run("create customer", lambda: _check_customer(storage, run_token)))
        _sleep_between_steps()

        checks.append(_run("create and retrieve order", lambda: _check_order(storage, run_token)))
        _sleep_between_steps()

        checks.append(
            _run(
                "update order status confirmed",
                lambda: _check_update_order_status(storage, run_token),
            )
        )
        _sleep_between_steps()

        checks.append(
            _run(
                "append and list stock movement",
                lambda: _check_stock_movement(storage, run_token),
            )
        )
        _sleep_between_steps()

        checks.append(_run("append parse log", lambda: _check_parse_log(storage, run_token)))
        _sleep_between_steps()
    finally:
        cleanup_report = _cleanup_rows(storage, run_token)

    print("\nCleanup:")
    for line in cleanup_report:
        print(f"  {line}")

    failed = [name for name, ok, _ in checks if not ok]

    if failed:
        print("\nFAILED checks:")
        for name in failed:
            print(f"  - {name}")
        return 1

    print("\nAll smoke checks passed.")
    return 0


def _make_storage() -> GoogleSheetsStorage:
    spreadsheet_id = os.getenv("GOOGLE_SHEETS_TEST_SPREADSHEET_ID")
    credentials_path = os.getenv(
        "GOOGLE_SHEETS_CREDENTIALS_PATH",
        "./credentials/service_account.json",
    )

    if not spreadsheet_id:
        raise RuntimeError("GOOGLE_SHEETS_TEST_SPREADSHEET_ID is not set.")

    production_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")
    if production_id and production_id == spreadsheet_id:
        raise RuntimeError(
            "GOOGLE_SHEETS_TEST_SPREADSHEET_ID must not equal "
            "GOOGLE_SHEETS_SPREADSHEET_ID."
        )

    return GoogleSheetsStorage(
        spreadsheet_id=spreadsheet_id,
        credentials_path=credentials_path,
    )


def _run(name: str, fn) -> tuple[str, bool, str]:
    try:
        fn()
    except Exception as error:
        print(f"FAIL — {name}: {type(error).__name__}: {error}")
        return name, False, str(error)

    print(f"PASS — {name}")
    return name, True, ""


def _check_bootstrap(storage: GoogleSheetsStorage) -> None:
    for tab_name, expected_headers in TABS.items():
        worksheet = storage._worksheet(tab_name)
        actual_headers = worksheet.row_values(1)

        if actual_headers != expected_headers:
            raise AssertionError(
                f"{tab_name} headers mismatch. "
                f"Expected {expected_headers}, got {actual_headers}."
            )


def _check_product(storage: GoogleSheetsStorage, run_token: str) -> None:
    product = Product(
        product_id=f"{run_token}prd_1",
        product_name="Smoke Product",
        unit_price=Decimal("1000"),
        current_stock=Decimal("20"),
        active=True,
    )

    storage.upsert_product(product)

    saved = storage.get_product(product.product_id)

    assert saved is not None
    assert saved.product_name == product.product_name
    assert saved.unit_price == Decimal("1000")
    assert saved.current_stock == Decimal("20")
    assert saved.active is True


def _check_customer(storage: GoogleSheetsStorage, run_token: str) -> None:
    customer = Customer(
        customer_id=f"{run_token}cus_1",
        customer_name="Smoke Customer",
        customer_phone=f"{run_token}3001234567",
    )

    storage.create_customer(customer)

    saved = storage.get_customer(customer.customer_id)
    by_phone = storage.get_customer_by_phone(f" {customer.customer_phone} ")

    assert saved is not None
    assert saved.customer_id == customer.customer_id
    assert by_phone is not None
    assert by_phone.customer_id == customer.customer_id


def _make_order(run_token: str) -> Order:
    order_id = f"{run_token}ord_1"

    items = [
        OrderItem(
            order_item_id=f"{run_token}oit_1",
            order_id=order_id,
            product_id=f"{run_token}prd_1",
            product_name_snapshot="Smoke Product",
            unit_snapshot="unidad",
            quantity=Decimal("2"),
            unit_price_snapshot=Decimal("1000"),
            line_total=Decimal("2000"),
            validation_status="ok",
        ),
        OrderItem(
            order_item_id=f"{run_token}oit_2",
            order_id=order_id,
            product_id=f"{run_token}prd_1",
            product_name_snapshot="Smoke Product",
            unit_snapshot="unidad",
            quantity=Decimal("3"),
            unit_price_snapshot=Decimal("1000"),
            line_total=Decimal("3000"),
            validation_status="ok",
        ),
    ]

    return Order(
        order_id=order_id,
        raw_message="Smoke order",
        status="draft",
        items=items,
        subtotal=Decimal("5000"),
        delivery_fee=Decimal("0"),
        total=Decimal("5000"),
    )


def _check_order(storage: GoogleSheetsStorage, run_token: str) -> None:
    order = _make_order(run_token)

    storage.create_order(order)

    saved = storage.get_order(order.order_id)

    assert saved is not None
    assert saved.order_id == order.order_id
    assert saved.status == "draft"
    assert len(saved.items) == 2
    assert saved.subtotal == Decimal("5000")
    assert saved.total == Decimal("5000")
    assert {item.order_item_id for item in saved.items} == {
        f"{run_token}oit_1",
        f"{run_token}oit_2",
    }


def _check_update_order_status(storage: GoogleSheetsStorage, run_token: str) -> None:
    order_id = f"{run_token}ord_1"
    confirmed_at = datetime.now(timezone.utc).replace(microsecond=123456)

    updated = storage.update_order_status(
        order_id,
        "confirmed",
        confirmed_at=confirmed_at,
    )
    saved = storage.get_order(order_id)

    assert updated.status == "confirmed"
    assert updated.confirmed_at == confirmed_at
    assert saved is not None
    assert saved.confirmed_at == confirmed_at


def _check_stock_movement(storage: GoogleSheetsStorage, run_token: str) -> None:
    movement = StockMovement(
        stock_movement_id=f"{run_token}mov_1",
        product_id=f"{run_token}prd_1",
        quantity_delta=Decimal("-2"),
        reason="sale",
        reference_id=f"{run_token}ord_1",
    )

    storage.append_stock_movement(movement)

    movements = storage.list_stock_movements(product_id=movement.product_id)

    assert len([m for m in movements if m.stock_movement_id == movement.stock_movement_id]) == 1


def _check_parse_log(storage: GoogleSheetsStorage, run_token: str) -> None:
    entry = ParseLogEntry(
        parse_id=f"{run_token}prs_1",
        raw_message="Smoke parse",
        parsed_json='{"items":[]}',
        model="smoke-model",
        prompt_version="smoke-prompt-v1",
        latency_ms=123,
        success=True,
        error=None,
)

    saved = storage.append_parse_log(entry)

    assert saved.parse_id == entry.parse_id
    assert saved.parsed_json == entry.parsed_json


def _cleanup_rows(storage: GoogleSheetsStorage, run_token: str) -> list[str]:
    report: list[str] = []

    for tab_name, headers in TABS.items():
        worksheet = storage._worksheet(tab_name)
        values = worksheet.get_all_values()

        id_column = PRIMARY_ID_COLUMNS[tab_name]
        id_col_index = headers.index(id_column)

        rows_to_delete = [
            row_index
            for row_index, row in enumerate(values[1:], start=2)
            if len(row) > id_col_index and str(row[id_col_index]).startswith(run_token)
        ]

        for row_index in reversed(rows_to_delete):
            worksheet.delete_rows(row_index)

        report.append(f"{tab_name}: deleted {len(rows_to_delete)} rows")

    return report


if __name__ == "__main__":
    sys.exit(main())