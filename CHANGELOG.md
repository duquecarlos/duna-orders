# Changelog
## M3.1 — Parse log prompt versioning

### Delivered

- Added `PROMPT_VERSION` in `src/duna_orders/parsing/prompts.py`.
- Added `prompt_version` to `ParseLogEntry`.
- Added `prompt_version` to the `parse_log` sheet schema.
- Updated `ParsingService` to persist the prompt version on both successful and failed parse attempts.
- Updated `GoogleSheetsStorage` parse log serialization for the new field.
- Updated tests and smoke script constructors for `ParseLogEntry`.

### Verified

- `python -m compileall src tests scripts` → OK.
- `pytest tests/ -v` → `30 passed, 13 deselected`.
- Live Google Sheets validation passed after manually migrating the test spreadsheet `parse_log` header.
- Google Sheets smoke script passed after the header migration.

### Notes

`prompt_version` is now part of parser auditability. Any future prompt change should update `PROMPT_VERSION` so parse logs can be tied back to the exact prompt version that produced them.

## M3 — Storage contract and live Google Sheets validation

### Delivered

- Implemented `GoogleSheetsStorage` as a concrete `StorageInterface` backend.
- Added Google Sheets persistence for:
  - products
  - customers
  - orders
  - order_items
  - stock_movements
  - parse_log
- Refactored storage tests into `tests/test_storage_contract.py`.
- Added parametrized storage contract tests:
  - memory backend runs by default
  - Google Sheets backend runs with the `live_sheets` marker
- Added hard separation between:
  - `GOOGLE_SHEETS_SPREADSHEET_ID` for runtime / production use
  - `GOOGLE_SHEETS_TEST_SPREADSHEET_ID` for live tests
- Added `run_token`-based isolation for live Sheets tests.
- Added session-scoped `GoogleSheetsStorage` for live tests to reduce setup overhead.
- Added session-end cleanup for rows created by live tests.
- Added `scripts/smoke_google_sheets.py` for end-to-end Sheets validation.

### Verified

- Default test suite:
  - `pytest tests/ -v`
  - result: `30 passed, 13 deselected`
- Live Google Sheets contract suite:
  - `pytest -m live_sheets -v`
  - result: `12 passed, 31 deselected`
- Google Sheets smoke script:
  - `python scripts/smoke_google_sheets.py`
  - passed twice consecutively

### Storage behavior confirmed under live Sheets

- `Product` duplicate ID uses upsert replacement.
- `Customer` duplicate ID raises `ValueError`.
- `Order` duplicate ID raises `ValueError`.
- `StockMovement` duplicate ID raises `ValueError`.
- `ParseLogEntry` duplicate `parse_id` raises `ValueError`.
- `update_order_status` with an unknown `order_id` raises `KeyError`.
- `get_product`, `get_customer`, and `get_order` return `None` for unknown IDs.
- `confirmed_at` persists correctly.
- Datetime round-trip preserved microsecond precision.
- Boolean round-trip passed through `Product.active`.
- `list_orders` works against Google Sheets.
- `list_stock_movements(product_id=...)` works against Google Sheets.

### Google Sheets quota finding

Initial live Sheets runs hit Google Sheets API 429 read quota errors.

Resolution for test workflows:

- Added session-scoped Sheets storage in the fixture.
- Added configurable delay through `LIVE_SHEETS_TEST_DELAY_S`.
- Final successful live test run used `LIVE_SHEETS_TEST_DELAY_S=8`.

No retry/backoff was added to production storage code.

### Known deferrals

- `GoogleSheetsStorage` has no retry/backoff layer for 429 or 5xx errors.
- `gspread` emits deprecation warnings for `worksheet.update(...)` argument order in:
  - `upsert_product`
  - `update_order_status`
- Live Sheets cleanup runs at session end only; crashed test runs may leave orphaned `test_run_*` rows.
- `parse_log` does not yet include `prompt_version`.

### Notes

Google Sheets is the current persistence backend for pilot validation, not the core architecture. `StorageInterface` remains the migration boundary for future database backends.