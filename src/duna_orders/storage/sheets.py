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
    ParseLogEntry,
    Product,
    StockMovement,
)
from duna_orders.storage.base import StorageInterface
from duna_orders.storage.exceptions import (
    StorageAuthError,
    StorageBackendError,
    StorageConfigError,
)
from duna_orders.storage.schema import CUSTOMERS_TAB, PRODUCTS_TAB, TABS


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
        raise NotImplementedError

    def get_order(self, order_id: str) -> Order | None:
        raise NotImplementedError

    def list_orders(
        self,
        *,
        status: str | None = None,
        since: datetime | None = None,
    ) -> list[Order]:
        raise NotImplementedError

    def update_order_status(
        self,
        order_id: str,
        status: str,
        confirmed_at: datetime | None = None,
    ) -> Order:
        raise NotImplementedError

    def append_stock_movement(self, movement: StockMovement) -> StockMovement:
        raise NotImplementedError

    def append_parse_log(self, entry: ParseLogEntry) -> ParseLogEntry:
        raise NotImplementedError

    def list_stock_movements(
        self,
        *,
        product_id: str | None = None,
    ) -> list[StockMovement]:
        raise NotImplementedError
