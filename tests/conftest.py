import os
import time
import uuid
from dataclasses import dataclass
from itertools import groupby
from operator import itemgetter

import gspread
import pytest
from duna_orders.config import settings
from duna_orders.storage.base import StorageInterface
from duna_orders.storage.memory import InMemoryStorage
from duna_orders.storage.schema import TABS
from duna_orders.storage.sheets import GoogleSheetsStorage

DEFAULT_TEST_TENANT_ID = "test-tenant"

@dataclass(frozen=True)
class StorageCase:
    backend: str
    storage: StorageInterface
    run_token: str


PRIMARY_ID_COLUMNS = {
    "products": "product_id",
    "customers": "customer_id",
    "orders": "order_id",
    "order_items": "order_item_id",
    "stock_movements": "stock_movement_id",
    "parse_log": "parse_id",
}


@pytest.fixture(scope="session")
def live_sheets_run_tokens() -> list[str]:
    return []


@pytest.fixture(scope="session")
def live_sheets_storage(
    live_sheets_run_tokens: list[str],
) -> GoogleSheetsStorage:
    
    test_spreadsheet_id = settings.google_sheets_test_spreadsheet_id

    if not test_spreadsheet_id:
        pytest.skip("GOOGLE_SHEETS_TEST_SPREADSHEET_ID is not set")

    production_spreadsheet_id = settings.google_sheets_spreadsheet_id
    if production_spreadsheet_id and test_spreadsheet_id == production_spreadsheet_id:
        pytest.fail(
            "GOOGLE_SHEETS_TEST_SPREADSHEET_ID must not equal "
            "GOOGLE_SHEETS_SPREADSHEET_ID. Use a separate test spreadsheet."
        )

    credentials_path = str(settings.google_sheets_credentials_path)

    storage = GoogleSheetsStorage(
        spreadsheet_id=test_spreadsheet_id,
        credentials_path=credentials_path,
    )

    try:
        yield storage
    finally:
        _cleanup_google_sheets_rows(storage, live_sheets_run_tokens)


@pytest.fixture(
    params=[
        "memory",
        pytest.param("sheets", marks=pytest.mark.live_sheets),
    ]
)
def storage_case(
    request: pytest.FixtureRequest,
    live_sheets_run_tokens: list[str],
) -> StorageCase:
    backend = request.param
    run_token = f"test_run_{uuid.uuid4().hex[:8]}_"

    if backend == "memory":
        yield StorageCase(
            backend=backend,
            storage=InMemoryStorage(),
            run_token=run_token,
        )
        return

    storage = request.getfixturevalue("live_sheets_storage")
    live_sheets_run_tokens.append(run_token)

    try:
        yield StorageCase(
            backend=backend,
            storage=storage,
            run_token=run_token,
        )
    finally:
        delay_s = float(os.getenv("LIVE_SHEETS_TEST_DELAY_S", "1.5"))
        time.sleep(delay_s)


def _cleanup_google_sheets_rows(
    storage: GoogleSheetsStorage,
    run_tokens: list[str],
) -> None:
    if not run_tokens:
        return

    prefixes = tuple(run_tokens)
    cleanup_report: list[str] = []

    try:
        for tab_name, headers in TABS.items():
            worksheet = storage._worksheet(tab_name)
            values = worksheet.get_all_values()

            id_column = PRIMARY_ID_COLUMNS[tab_name]
            id_col_index = headers.index(id_column)

            rows_to_delete = [
                row_index
                for row_index, row in enumerate(values[1:], start=2)
                if len(row) > id_col_index and str(row[id_col_index]).startswith(prefixes)
            ]

            if not rows_to_delete:
                cleanup_report.append(f"{tab_name}: 0 rows")
                continue

            ranges = _contiguous_ranges(rows_to_delete)

            for start, end in reversed(ranges):
                worksheet.delete_rows(start, end)

            cleanup_report.append(f"{tab_name}: {len(rows_to_delete)} rows")

    except gspread.exceptions.APIError as error:
        pending = "; ".join(cleanup_report) if cleanup_report else "no cleanup completed"
        pytest.fail(
            "Google Sheets cleanup hit APIError. "
            f"Completed before failure: {pending}. "
            f"Manual cleanup may be needed for tokens: {', '.join(run_tokens)}. "
            f"Original error: {error}"
        )


def _contiguous_ranges(row_indices: list[int]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []

    for _, group in groupby(
        enumerate(sorted(row_indices)),
        key=lambda pair: pair[1] - pair[0],
    ):
        rows = list(map(itemgetter(1), group))
        ranges.append((rows[0], rows[-1]))

    return ranges