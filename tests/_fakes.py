from duna_orders.domain.models import (
    DraftOrderRequest,
    ParseResult,
    Product,
)
from duna_orders.parsing.base import ParserInterface
from tests.conftest import DEFAULT_TEST_TENANT_ID
from typing import Any

from duna_orders.storage.schema import TABS
from duna_orders.storage.sheets import GoogleSheetsStorage

class MockParser(ParserInterface):
    """Deterministic parser for tests. Configurable via constructor."""

    def __init__(
        self,
        result: ParseResult | None = None,
        raise_error: Exception | None = None,
        model_name: str = "mock-parser",
    ) -> None:
        self._result = result
        self._raise_error = raise_error
        self._model_name = model_name
        self.calls: list[tuple[str, list[Product]]] = []

    @property
    def model_name(self) -> str:
        return self._model_name

    def parse(self, raw_message: str, products: list[Product]) -> ParseResult:
        self.calls.append((raw_message, list(products)))

        if self._raise_error is not None:
            raise self._raise_error

        if self._result is None:
            return ParseResult(
                request=DraftOrderRequest(
                    tenant_id=DEFAULT_TEST_TENANT_ID,
                    raw_message=raw_message,
                    customer_name="",
                    items=[],
                ),
                warnings=[],
                model=self._model_name,
                latency_ms=0,
                raw_response="{}",
            )

        return self._result
class FakeWorksheet:
    def __init__(
        self,
        title: str,
        records: list[dict[str, Any]] | None = None,
    ) -> None:
        self.title = title
        self._records = [dict(record) for record in records or []]
        self.get_all_records_call_count = 0

    def get_all_records(self) -> list[dict[str, Any]]:
        self.get_all_records_call_count += 1
        return [dict(record) for record in self._records]

    def set_records(self, records: list[dict[str, Any]]) -> None:
        self._records = [dict(record) for record in records]


class FakeSpreadsheet:
    def __init__(
        self,
        records_by_tab: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        records_by_tab = records_by_tab or {}
        self._worksheets = {
            tab_name: FakeWorksheet(
                title=tab_name,
                records=records_by_tab.get(tab_name, []),
            )
            for tab_name in TABS
        }

    def worksheet(self, tab_name: str) -> FakeWorksheet:
        return self._worksheets[tab_name]

    def set_records_by_tab(
        self,
        records_by_tab: dict[str, list[dict[str, Any]]],
    ) -> None:
        for tab_name, records in records_by_tab.items():
            self._worksheets[tab_name].set_records(records)

    def read_count(self, tab_name: str) -> int:
        return self._worksheets[tab_name].get_all_records_call_count


def make_fake_google_sheets_storage(
    records_by_tab: dict[str, list[dict[str, Any]]] | None = None,
) -> GoogleSheetsStorage:
    storage = object.__new__(GoogleSheetsStorage)
    storage._spreadsheet_id = "fake-spreadsheet-id"
    storage._credentials_path = "fake-credentials.json"
    storage._spreadsheet = FakeSpreadsheet(records_by_tab)
    return storage