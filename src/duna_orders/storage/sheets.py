import json
import os
from datetime import datetime
from decimal import Decimal
from typing import Any

import gspread
from google.auth.exceptions import GoogleAuthError
from gspread.utils import rowcol_to_a1

from duna_orders.domain.models import (
    Customer,
    Order,
    OrderItem,
    ParseLogEntry,
    Product,
    StockMovement,
    utc_now,
)
from duna_orders.storage.base import StorageInterface
from duna_orders.storage.exceptions import (
    StorageAuthError,
    StorageBackendError,
    StorageConfigError,
)
from duna_orders.storage.schema import (
    CUSTOMERS_TAB,
    ORDER_ITEMS_TAB,
    ORDERS_TAB,
    PARSE_LOG_TAB,
    PRODUCTS_TAB,
    STOCK_MOVEMENTS_TAB,
    TABS,
)

class GoogleSheetsStorage(StorageInterface):
    def __init__(
        self,
        spreadsheet_id: str | None = None,
        credentials_path: str | None = None,
    ) -> None:
        self._spreadsheet_id = spreadsheet_id or os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")
        self._credentials_path = credentials_path or os.getenv("GOOGLE_SHEETS_CREDENTIALS_PATH")

        if not self._spreadsheet_id:
            raise StorageConfigError("GOOGLE_SHEETS_SPREADSHEET_ID is not set.")

        if not self._credentials_path:
            raise StorageConfigError("GOOGLE_SHEETS_CREDENTIALS_PATH is not set.")

        if not os.path.exists(self._credentials_path):
            raise StorageConfigError(
                f"Google Sheets credentials file not found: {self._credentials_path}"
            )

        try:
            self._client = gspread.service_account(filename=self._credentials_path)
            self._spreadsheet = self._client.open_by_key(self._spreadsheet_id)
        except GoogleAuthError as error:
            raise StorageAuthError(str(error)) from error
        except gspread.exceptions.GSpreadException as error:
            raise StorageBackendError(str(error)) from error
        except Exception as error:
            raise StorageBackendError(str(error)) from error

        self._bootstrap()

    def _bootstrap(self) -> None:
        worksheets = {worksheet.title: worksheet for worksheet in self._spreadsheet.worksheets()}

        for tab_name, expected_headers in TABS.items():
            worksheet = worksheets.get(tab_name)

            if worksheet is None:
                worksheet = self._spreadsheet.add_worksheet(
                    title=tab_name,
                    rows=1000,
                    cols=len(expected_headers),
                )
                worksheet.append_row(expected_headers)
                continue

            actual_headers = worksheet.row_values(1)

            if actual_headers != expected_headers:
                raise StorageConfigError(
                    f"Header mismatch in tab '{tab_name}'. "
                    f"Expected {expected_headers}, got {actual_headers}."
                )

    def _worksheet(self, tab_name: str) -> gspread.Worksheet:
        try:
            return self._spreadsheet.worksheet(tab_name)
        except gspread.exceptions.GSpreadException as error:
            raise StorageBackendError(str(error)) from error

    def _records(self, tab_name: str) -> list[dict[str, Any]]:
        try:
            return self._worksheet(tab_name).get_all_records()
        except gspread.exceptions.GSpreadException as error:
            raise StorageBackendError(str(error)) from error

    def _find_row_index(
        self,
        *,
        tab_name: str,
        id_column: str,
        id_value: str,
    ) -> int | None:
        worksheet = self._worksheet(tab_name)
        headers = TABS[tab_name]
        id_col_index = headers.index(id_column) + 1

        try:
            values = worksheet.col_values(id_col_index)
        except gspread.exceptions.GSpreadException as error:
            raise StorageBackendError(str(error)) from error

        for row_index, value in enumerate(values, start=1):
            if row_index == 1:
                continue

            if str(value) == id_value:
                return row_index

        return None

    @staticmethod
    def _empty_to_none(value: Any) -> Any:
        return None if value == "" else value

    @staticmethod
    def _to_decimal(value: Any) -> Decimal:
        if value in ("", None):
            return Decimal("0")
        return Decimal(str(value))

    @staticmethod
    def _to_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value

        if isinstance(value, (int, float)):
            return bool(value)

        return str(value).strip().lower() in {"true", "1", "yes", "s�", "si"}

    @staticmethod
    def _to_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value))

    @staticmethod
    def _optional_datetime(value: Any) -> datetime | None:
        if value in ("", None):
            return None
        return GoogleSheetsStorage._to_datetime(value)

    @staticmethod
    def _json_list(value: Any) -> list[str]:
        if value in ("", None):
            return []

        if isinstance(value, list):
            return value

        return list(json.loads(str(value)))

    @staticmethod
    def _decimal_text(value: Decimal) -> str:
        return str(value)

    @staticmethod
    def _datetime_text(value: datetime) -> str:
        return value.isoformat()

    @staticmethod
    def _optional_text(value: Any) -> Any:
        return "" if value is None else value

    def _product_to_row(self, product: Product) -> list[Any]:
        return [
            product.product_id,
            product.product_name,
            json.dumps(product.aliases, ensure_ascii=False),
            self._optional_text(product.category),
            product.unit,
            self._decimal_text(product.unit_price),
            product.active,
            self._decimal_text(product.current_stock),
            self._decimal_text(product.min_stock),
            self._optional_text(product.notes),
            self._datetime_text(product.created_at),
            self._datetime_text(product.updated_at),
        ]

    def _product_from_record(self, record: dict[str, Any]) -> Product:
        return Product.model_validate(
            {
                "product_id": record["product_id"],
                "product_name": record["product_name"],
                "aliases": self._json_list(record["aliases"]),
                "category": self._empty_to_none(record["category"]),
                "unit": record["unit"],
                "unit_price": self._to_decimal(record["unit_price"]),
                "active": self._to_bool(record["active"]),
                "current_stock": self._to_decimal(record["current_stock"]),
                "min_stock": self._to_decimal(record["min_stock"]),
                "notes": self._empty_to_none(record["notes"]),
                "created_at": self._to_datetime(record["created_at"]),
                "updated_at": self._to_datetime(record["updated_at"]),
            }
        )

    def _customer_to_row(self, customer: Customer) -> list[Any]:
        return [
            customer.customer_id,
            customer.customer_name,
            self._optional_text(customer.customer_phone),
            self._optional_text(customer.default_address),
            self._optional_text(customer.notes),
            self._datetime_text(customer.created_at),
            self._datetime_text(customer.updated_at),
            self._optional_text(
                self._datetime_text(customer.last_order_at)
                if customer.last_order_at is not None
                else None
            ),
        ]

    def _customer_from_record(self, record: dict[str, Any]) -> Customer:
        return Customer.model_validate(
            {
                "customer_id": record["customer_id"],
                "customer_name": record["customer_name"],
                "customer_phone": self._empty_to_none(record["customer_phone"]),
                "default_address": self._empty_to_none(record["default_address"]),
                "notes": self._empty_to_none(record["notes"]),
                "created_at": self._to_datetime(record["created_at"]),
                "updated_at": self._to_datetime(record["updated_at"]),
                "last_order_at": self._optional_datetime(record["last_order_at"]),
            }
        )
    def _order_item_to_row(self, item: OrderItem) -> list[Any]:
        return [
            item.order_item_id,
            item.order_id,
            self._optional_text(item.product_id),
            item.product_name_snapshot,
            item.unit_snapshot,
            self._decimal_text(item.quantity),
            self._decimal_text(item.unit_price_snapshot),
            self._decimal_text(item.line_total),
            item.validation_status,
            self._optional_text(item.notes),
        ]

    def _order_item_from_record(self, record: dict[str, Any]) -> OrderItem:
        return OrderItem.model_validate(
            {
                "order_item_id": record["order_item_id"],
                "order_id": record["order_id"],
                "product_id": self._empty_to_none(record["product_id"]),
                "product_name_snapshot": record["product_name_snapshot"],
                "unit_snapshot": record["unit_snapshot"],
                "quantity": self._to_decimal(record["quantity"]),
                "unit_price_snapshot": self._to_decimal(record["unit_price_snapshot"]),
                "line_total": self._to_decimal(record["line_total"]),
                "validation_status": record["validation_status"],
                "notes": self._empty_to_none(record["notes"]),
            }
        )

    def _order_to_row(self, order: Order) -> list[Any]:
        return [
            order.order_id,
            self._datetime_text(order.created_at),
            self._datetime_text(order.updated_at),
            self._optional_text(order.customer_id),
            self._optional_text(order.customer_name_snapshot),
            self._optional_text(order.customer_phone_snapshot),
            order.raw_message,
            order.status,
            self._optional_text(
                self._datetime_text(order.confirmed_at)
                if order.confirmed_at is not None
                else None
            ),
            self._decimal_text(order.subtotal),
            self._decimal_text(order.delivery_fee),
            self._decimal_text(order.total),
            self._optional_text(order.delivery_date),
            self._optional_text(order.delivery_address),
            self._optional_text(order.notes),
            self._optional_text(order.confirmation_message),
            self._optional_text(order.created_by),
        ]

    def _order_from_record(
        self,
        record: dict[str, Any],
        items: list[OrderItem],
    ) -> Order:
        return Order.model_validate(
            {
                "order_id": record["order_id"],
                "created_at": self._to_datetime(record["created_at"]),
                "updated_at": self._to_datetime(record["updated_at"]),
                "customer_id": self._empty_to_none(record["customer_id"]),
                "customer_name_snapshot": self._empty_to_none(
                    record["customer_name_snapshot"]
                ),
                "customer_phone_snapshot": self._empty_to_none(
                    record["customer_phone_snapshot"]
                ),
                "raw_message": record["raw_message"],
                "status": record["status"],
                "confirmed_at": self._optional_datetime(record["confirmed_at"]),
                "items": items,
                "subtotal": self._to_decimal(record["subtotal"]),
                "delivery_fee": self._to_decimal(record["delivery_fee"]),
                "total": self._to_decimal(record["total"]),
                "delivery_date": self._empty_to_none(record["delivery_date"]),
                "delivery_address": self._empty_to_none(record["delivery_address"]),
                "notes": self._empty_to_none(record["notes"]),
                "confirmation_message": self._empty_to_none(
                    record["confirmation_message"]
                ),
                "created_by": self._empty_to_none(record["created_by"]),
            }
        )
    
    def _stock_movement_to_row(self, movement: StockMovement) -> list[Any]:
        return [
            movement.stock_movement_id,
            self._datetime_text(movement.created_at),
            movement.product_id,
            self._decimal_text(movement.quantity_delta),
            movement.reason,
            self._optional_text(movement.reference_id),
            self._optional_text(movement.notes),
            self._optional_text(movement.created_by),
        ]

    def _stock_movement_from_record(self, record: dict[str, Any]) -> StockMovement:
        return StockMovement.model_validate(
            {
                "stock_movement_id": record["stock_movement_id"],
                "created_at": self._to_datetime(record["created_at"]),
                "product_id": record["product_id"],
                "quantity_delta": self._to_decimal(record["quantity_delta"]),
                "reason": record["reason"],
                "reference_id": self._empty_to_none(record["reference_id"]),
                "notes": self._empty_to_none(record["notes"]),
                "created_by": self._empty_to_none(record["created_by"]),
            }
        )
    def _parse_log_entry_to_row(self, entry: ParseLogEntry) -> list[Any]:
        return [
            entry.parse_id,
            self._datetime_text(entry.created_at),
            entry.raw_message,
            entry.parsed_json,
            entry.model,
            entry.latency_ms,
            "true" if entry.success else "false",
            self._optional_text(entry.error),
        ]

    def _parse_log_entry_from_record(self, record: dict[str, Any]) -> ParseLogEntry:
        return ParseLogEntry.model_validate(
            {
                "parse_id": record["parse_id"],
                "created_at": self._to_datetime(record["created_at"]),
                "raw_message": record["raw_message"],
                "parsed_json": record["parsed_json"],
                "model": record["model"],
                "latency_ms": int(record["latency_ms"]),
                "success": self._to_bool(record["success"]),
                "error": self._empty_to_none(record["error"]),
            }
        )
    def list_products(self, *, active_only: bool = True) -> list[Product]:
        products = [self._product_from_record(record) for record in self._records(PRODUCTS_TAB)]

        if active_only:
            products = [product for product in products if product.active]

        return products

    def get_product(self, product_id: str) -> Product | None:
        for product in self.list_products(active_only=False):
            if product.product_id == product_id:
                return product

        return None

    def upsert_product(self, product: Product) -> Product:
        worksheet = self._worksheet(PRODUCTS_TAB)
        row = self._product_to_row(product)
        row_index = self._find_row_index(
            tab_name=PRODUCTS_TAB,
            id_column="product_id",
            id_value=product.product_id,
        )

        try:
            if row_index is None:
                worksheet.append_row(row)
            else:
                start = rowcol_to_a1(row_index, 1)
                end = rowcol_to_a1(row_index, len(TABS[PRODUCTS_TAB]))
                worksheet.update(f"{start}:{end}", [row])
        except gspread.exceptions.GSpreadException as error:
            raise StorageBackendError(str(error)) from error

        return product.model_copy(deep=True)

    def list_customers(self) -> list[Customer]:
        return [
            self._customer_from_record(record)
            for record in self._records(CUSTOMERS_TAB)
        ]

    def get_customer(self, customer_id: str) -> Customer | None:
        for customer in self.list_customers():
            if customer.customer_id == customer_id:
                return customer

        return None

    def get_customer_by_phone(self, phone: str) -> Customer | None:
        normalized_phone = phone.strip()

        if not normalized_phone:
            return None

        for customer in self.list_customers():
            if (
                customer.customer_phone is not None
                and customer.customer_phone.strip() == normalized_phone
            ):
                return customer

        return None

    def create_customer(self, customer: Customer) -> Customer:
        worksheet = self._worksheet(CUSTOMERS_TAB)
        row_index = self._find_row_index(
            tab_name=CUSTOMERS_TAB,
            id_column="customer_id",
            id_value=customer.customer_id,
        )

        if row_index is not None:
            raise ValueError(f"Customer already exists: {customer.customer_id}")

        row = self._customer_to_row(customer)

        try:
            worksheet.append_row(row)
        except gspread.exceptions.GSpreadException as error:
            raise StorageBackendError(str(error)) from error

        return customer.model_copy(deep=True)

    def create_order(self, order: Order) -> Order:
        existing_row = self._find_row_index(
            tab_name=ORDERS_TAB,
            id_column="order_id",
            id_value=order.order_id,
        )

        if existing_row is not None:
            raise ValueError(f"Order already exists: {order.order_id}")

        item_rows = [self._order_item_to_row(item) for item in order.items]
        order_row = self._order_to_row(order)

        try:
            if item_rows:
                self._worksheet(ORDER_ITEMS_TAB).append_rows(item_rows)

            self._worksheet(ORDERS_TAB).append_row(order_row)
        except gspread.exceptions.GSpreadException as error:
            raise StorageBackendError(str(error)) from error

        return order.model_copy(deep=True)

    def get_order(self, order_id: str) -> Order | None:
        for order in self.list_orders():
            if order.order_id == order_id:
                return order

        return None

    def list_orders(
        self,
        *,
        status: str | None = None,
        since: datetime | None = None,
    ) -> list[Order]:
        item_records = self._records(ORDER_ITEMS_TAB)
        items_by_order_id: dict[str, list[OrderItem]] = {}

        for record in item_records:
            item = self._order_item_from_record(record)
            items_by_order_id.setdefault(item.order_id, []).append(item)

        orders = [
            self._order_from_record(
                record,
                items_by_order_id.get(record["order_id"], []),
            )
            for record in self._records(ORDERS_TAB)
        ]

        if status is not None:
            orders = [order for order in orders if order.status == status]

        if since is not None:
            orders = [order for order in orders if order.created_at >= since]

        return orders

    def update_order_status(
        self,
        order_id: str,
        status: str,
        confirmed_at: datetime | None = None,
    ) -> Order:
        order = self.get_order(order_id)

        if order is None:
            raise KeyError(f"Order not found: {order_id}")

        updates: dict[str, Any] = {
            "status": status,
            "updated_at": utc_now(),
        }

        if confirmed_at is not None:
            updates["confirmed_at"] = confirmed_at

        updated_order = order.model_copy(update=updates, deep=True)
        row = self._order_to_row(updated_order)

        row_index = self._find_row_index(
            tab_name=ORDERS_TAB,
            id_column="order_id",
            id_value=order_id,
        )

        if row_index is None:
            raise KeyError(f"Order not found: {order_id}")

        try:
            start = rowcol_to_a1(row_index, 1)
            end = rowcol_to_a1(row_index, len(TABS[ORDERS_TAB]))
            self._worksheet(ORDERS_TAB).update(f"{start}:{end}", [row])
        except gspread.exceptions.GSpreadException as error:
            raise StorageBackendError(str(error)) from error

        return updated_order.model_copy(deep=True)
    
    def append_stock_movement(self, movement: StockMovement) -> StockMovement:
        row_index = self._find_row_index(
            tab_name=STOCK_MOVEMENTS_TAB,
            id_column="stock_movement_id",
            id_value=movement.stock_movement_id,
        )

        if row_index is not None:
            raise ValueError(
                f"Stock movement already exists: {movement.stock_movement_id}"
            )

        row = self._stock_movement_to_row(movement)

        try:
            self._worksheet(STOCK_MOVEMENTS_TAB).append_row(row)
        except gspread.exceptions.GSpreadException as error:
            raise StorageBackendError(str(error)) from error

        return movement.model_copy(deep=True)

    def append_parse_log(self, entry: ParseLogEntry) -> ParseLogEntry:
        row_index = self._find_row_index(
            tab_name=PARSE_LOG_TAB,
            id_column="parse_id",
            id_value=entry.parse_id,
        )

        if row_index is not None:
            raise ValueError(f"Parse log already exists: {entry.parse_id}")

        row = self._parse_log_entry_to_row(entry)

        try:
            self._worksheet(PARSE_LOG_TAB).append_row(row)
        except gspread.exceptions.GSpreadException as error:
            raise StorageBackendError(str(error)) from error

        return entry.model_copy(deep=True)

    def list_stock_movements(
        self,
        *,
        product_id: str | None = None,
    ) -> list[StockMovement]:
        movements = [
            self._stock_movement_from_record(record)
            for record in self._records(STOCK_MOVEMENTS_TAB)
        ]

        if product_id is not None:
            movements = [
                movement
                for movement in movements
                if movement.product_id == product_id
            ]

        return movements