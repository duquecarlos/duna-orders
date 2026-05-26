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
import re
from duna_orders.storage.sheets_cache import SheetsRecordsCache

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
        self._next_get_all_records_error: Exception | None = None

    def get_all_records(self) -> list[dict[str, Any]]:
        self.get_all_records_call_count += 1

        if self._next_get_all_records_error is not None:
            error = self._next_get_all_records_error
            self._next_get_all_records_error = None
            raise error

        return [dict(record) for record in self._records]

    def fail_next_get_all_records(self, error: Exception) -> None:
        self._next_get_all_records_error = error

    def set_records(self, records: list[dict[str, Any]]) -> None:
        self._records = [dict(record) for record in records]

    def append_row(self, row: list[object]) -> None:
        self._records.append(dict(zip(TABS[self.title], row)))

    def append_rows(self, rows: list[list[object]]) -> None:
        for row in rows:
            self.append_row(row)

    def col_values(self, col_index: int) -> list[object]:
        header = TABS[self.title][col_index - 1]
        return [header] + [record.get(header, "") for record in self._records]

    def update(self, *, values: list[list[object]], range_name: str) -> None:
        match = re.search(r"\d+", range_name)

        if match is None:
            raise ValueError(f"Cannot parse row index from range: {range_name}")

        record_index = int(match.group()) - 2

        if record_index < 0 or record_index >= len(self._records):
            raise IndexError(f"Row out of range: {range_name}")

        self._records[record_index] = dict(zip(TABS[self.title], values[0]))

    def reset_read_count(self) -> None:
        self.get_all_records_call_count = 0


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
    def reset_read_counts(self) -> None:
        for worksheet in self._worksheets.values():
            worksheet.reset_read_count()
    def fail_next_get_all_records(self, tab_name: str, error: Exception) -> None:
        self._worksheets[tab_name].fail_next_get_all_records(error)


def make_fake_google_sheets_storage(
    records_by_tab: dict[str, list[dict[str, Any]]] | None = None,
    *,
    spreadsheet_id: str = "fake-spreadsheet-id",
    time_source: Any | None = None,
) -> GoogleSheetsStorage:
    storage = object.__new__(GoogleSheetsStorage)
    storage._spreadsheet_id = spreadsheet_id
    storage._credentials_path = "fake-credentials.json"
    storage._spreadsheet = FakeSpreadsheet(records_by_tab)
    storage._records_cache = SheetsRecordsCache(
        time_source=time_source if time_source is not None else lambda: 0.0,
    )
    return storage