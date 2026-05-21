# Duna Orders

## 1. Project overview

Duna Orders is a Streamlit-based order control system for small businesses that currently manage sales through informal WhatsApp conversations.

The application converts messy customer messages into structured draft orders, keeps a human review step before confirmation, and records the operational impact of each confirmed order through inventory updates and stock movements.

> *AI suggests. Human confirms. Storage preserves the record.*

The current version supports manual and AI-assisted order entry, an order workflow from draft to confirmation, swappable storage backends (in-memory and Google Sheets), and live validation against real Sheets data.

M3 milestone details and the future roadmap are tracked outside this file.

## 2. Architecture snapshot

Duna Orders is organized around clear boundaries between user interface, business logic, parsing, and persistence.

```text
Streamlit UI
→ Services
→ StorageInterface
→ Storage backend
```

The current storage backends are:

```text
InMemoryStorage      → local, fast, test-friendly backend
GoogleSheetsStorage  → spreadsheet-backed persistence for pilot use
```

The storage boundary is intentionally backend-agnostic. Google Sheets is the current persistent backend for pilot validation, but the application is not designed around Sheets-specific behavior. Services depend on `StorageInterface`, so a future database backend can replace `GoogleSheetsStorage` without changing order creation, confirmation, parsing, or UI workflows.

The order flow is intentionally human-in-the-loop:

```text
Raw WhatsApp message
→ parser suggests DraftOrderRequest
→ human reviews/edits
→ OrderService creates draft
→ human confirms
→ OrderService updates stock and writes stock movements
→ storage preserves orders, items, stock movements, and parse logs
```

The parser layer is separated from order creation. A parser can suggest structured items, but it does not create orders, update stock, or write business state directly. This keeps AI output reviewable before it affects inventory.

Business rules live in services. Storage implementations persist domain objects and reconstruct them, but they do not generate business IDs, recompute totals, validate status transitions, compute stock deltas, or re-fetch snapshot data.

## 3. Storage semantics

Storage backends implement the same `StorageInterface` contract. The application currently supports:

```text
InMemoryStorage      → local, fast, test-friendly backend
GoogleSheetsStorage  → persistent spreadsheet backend for pilot workflows
```

Storage is treated as a pure persistence layer. It stores and reconstructs domain objects, but it does not own business rules.

Storage backends must not:

- generate business IDs
- recompute order totals
- re-fetch product snapshot data
- validate order status transitions
- compute stock deltas
- silently repair invalid business state

Those responsibilities belong to the service layer.

### Migration boundary

`StorageInterface` is the contract that protects the rest of the application from backend-specific details. `GoogleSheetsStorage` must behave like `InMemoryStorage`, and any future database backend must preserve the same contract.

The intended migration path is additive:

```text
InMemoryStorage
GoogleSheetsStorage
FutureDatabaseStorage
```

rather than a rewrite of services or domain logic.

Backend-specific limitations, such as Google Sheets quotas, worksheet headers, row cleanup, or API-specific error behavior, should stay inside storage implementations and test utilities. They should not leak into services, parsers, or UI code.

### Duplicate-ID behavior

Duplicate-ID behavior is intentionally asymmetric:

| Entity | Method | Duplicate behavior | Rationale |
|---|---|---|---|
| `Product` | `upsert_product` | Replaces existing record | Products are catalog-like and should be idempotently editable. |
| `Customer` | `create_customer` | Raises `ValueError` | Customers are identity records; accidental overwrites should be explicit. |
| `Order` | `create_order` | Raises `ValueError` | Orders are historical records and should not be overwritten. |
| `StockMovement` | `append_stock_movement` | Raises `ValueError` | Stock movements are append-only audit records. |
| `ParseLogEntry` | `append_parse_log` | Raises `ValueError` | Parse logs are append-only audit records. |

This is part of the storage contract, not an implementation accident. Future storage backends must preserve the same behavior unless the contract is changed across all backends and tests.

### Missing-ID behavior

Lookup methods return `None` when the entity does not exist:

```text
get_product(...)
get_customer(...)
get_order(...)
```

Mutation methods that require an existing entity raise an error when the target does not exist:

```text
update_order_status(...) → KeyError for unknown order_id
```

### Google Sheets behavior

`GoogleSheetsStorage` stores each entity in its corresponding worksheet tab. Multi-row objects, such as orders, are reconstructed by joining related rows in memory.

For orders:

```text
orders tab       → order header
order_items tab  → order line items
```

`create_order` writes `order_items` first and the `orders` row last. The order row is the visible commit point. If a partial failure happens before the order row is written, orphan item rows may exist, but no visible phantom order appears in `list_orders`.

This trade-off is accepted for the pilot stage because Google Sheets does not provide database-style transactions.

## 4. Repository structure

```text
duna-orders/
├── pages/
│   └── 1_New_Order.py
├── scripts/
│   ├── parser_smoke_test.py
│   └── smoke_google_sheets.py
├── src/
│   └── duna_orders/
│       ├── domain/
│       │   └── models.py
│       ├── parsing/
│       │   ├── anthropic_parser.py
│       │   ├── base.py
│       │   ├── exceptions.py
│       │   └── prompts.py
│       ├── services/
│       │   ├── orders.py
│       │   └── parsing.py
│       ├── storage/
│       │   ├── base.py
│       │   ├── memory.py
│       │   ├── schema.py
│       │   └── sheets.py
│       ├── config.py
│       └── ids.py
├── tests/
│   ├── integration/
│   │   └── test_anthropic_parser_live.py
│   ├── conftest.py
│   ├── test_orders_service.py
│   ├── test_parsing_service.py
│   └── test_storage_contract.py
├── .env.example
├── DECISIONS.md
├── pyproject.toml
└── README.md
```

Key files:

| Path | Purpose |
|---|---|
| `src/duna_orders/domain/models.py` | Pydantic domain models. |
| `src/duna_orders/services/orders.py` | Draft creation, confirmation, stock validation, stock movements. |
| `src/duna_orders/services/parsing.py` | Parser orchestration and parse logging. |
| `src/duna_orders/storage/base.py` | `StorageInterface` contract. |
| `src/duna_orders/storage/memory.py` | In-memory backend. |
| `src/duna_orders/storage/sheets.py` | Google Sheets backend. |
| `src/duna_orders/storage/schema.py` | Worksheet names, headers, enum constants. |
| `tests/test_storage_contract.py` | Shared storage contract tests for memory and Sheets. |
| `scripts/smoke_google_sheets.py` | End-to-end live Google Sheets smoke script. |

## 5. Prerequisites

### Runtime

- Python 3.11+
- PowerShell on Windows, or equivalent shell
- Git
- Google Cloud project for Sheets API access
- Google service account with JSON credentials
- A separate Google Sheet for live storage tests

### Google Cloud APIs

Enable these APIs in the Google Cloud project used by the service account:

```text
Google Sheets API
Google Drive API
```

### Service account

Create a service account and download its JSON key. Save it locally as:

```text
credentials/service_account.json
```

Do not commit this file.

The service account email must be shared as **Editor** on any spreadsheet used by `GoogleSheetsStorage`.

Recommended test spreadsheet name:

```text
Duna Orders - TEST STORAGE
```

The test spreadsheet must be separate from any production or client spreadsheet.

## 6. Environment setup

Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install the project in editable mode:

```powershell
pip install -e ".[dev]"
```

Install runtime/test dependencies used by the current MVP:

```powershell
pip install anthropic gspread google-auth pydantic pydantic-settings pytest pytest-mock streamlit tenacity
```

Create a local `.env` file from the example:

```powershell
Copy-Item ".env.example" ".env"
```

Required local variables:

```env
# --- App ---
APP_ENV=dev
LOG_LEVEL=INFO
DEFAULT_TIMEZONE=America/Bogota
DEFAULT_CURRENCY=COP

# --- LLM ---
LLM_PROVIDER=anthropic
LLM_MODEL=claude-sonnet-4-5
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
LLM_MAX_TOKENS=1024
LLM_TEMPERATURE=0.0

# --- Google Sheets ---
GOOGLE_SHEETS_CREDENTIALS_PATH=./credentials/service_account.json

# Production / runtime spreadsheet ID
GOOGLE_SHEETS_SPREADSHEET_ID=

# Separate spreadsheet for live_sheets tests; must NOT equal the production ID
GOOGLE_SHEETS_TEST_SPREADSHEET_ID=

ACTIVE_CLIENT_SHEET_ID=
ACTIVE_CLIENT_NAME=demo
```

Important rules:

```text
- Do not commit .env.
- Do not commit service_account.json.
- Do not reuse the production spreadsheet ID as the test spreadsheet ID.
- Keep GOOGLE_SHEETS_TEST_SPREADSHEET_ID separate from GOOGLE_SHEETS_SPREADSHEET_ID.
```

For one-off live test runs, environment variables can also be set directly in PowerShell:

```powershell
$env:GOOGLE_SHEETS_TEST_SPREADSHEET_ID = "PASTE_TEST_SPREADSHEET_ID_HERE"
$env:GOOGLE_SHEETS_CREDENTIALS_PATH = ".\credentials\service_account.json"
$env:LIVE_SHEETS_TEST_DELAY_S = "8"
Remove-Item Env:\GOOGLE_SHEETS_SPREADSHEET_ID -ErrorAction SilentlyContinue
```

## 7. Google Sheets configuration

`GoogleSheetsStorage` expects a spreadsheet with these tabs:

```text
products
customers
orders
order_items
stock_movements
parse_log
```

Headers are defined in:

```text
src/duna_orders/storage/schema.py
```

On initialization, `GoogleSheetsStorage` bootstraps the spreadsheet:

```text
- If a required tab is missing, it is created with the expected headers.
- If a tab exists with headers that do not match the schema, initialization fails.
```

This keeps spreadsheet structure explicit and prevents silent drift between code and sheet columns.

### Production vs test spreadsheet IDs

The default storage constructor uses:

```text
GOOGLE_SHEETS_SPREADSHEET_ID
```

for production/runtime storage.

Live test fixtures use:

```text
GOOGLE_SHEETS_TEST_SPREADSHEET_ID
```

and pass that ID explicitly into `GoogleSheetsStorage`.

The live test fixture fails loudly if both variables are set to the same value. This prevents accidentally running destructive test cleanup against a production or client sheet.

## 8. Testing

### 8.1 Unit tests

Default tests exclude external API calls.

Run:

```powershell
pytest tests/ -v
```

This runs memory-backed contract tests, service tests, and parser service tests. It excludes:

```text
live_api
live_sheets
```

through the default pytest configuration.

### 8.2 Live Google Sheets tests

Live Sheets tests run the same storage contract against `GoogleSheetsStorage`.

Set the test spreadsheet environment:

```powershell
$env:GOOGLE_SHEETS_TEST_SPREADSHEET_ID = "PASTE_TEST_SPREADSHEET_ID_HERE"
$env:GOOGLE_SHEETS_CREDENTIALS_PATH = ".\credentials\service_account.json"
$env:LIVE_SHEETS_TEST_DELAY_S = "8"
Remove-Item Env:\GOOGLE_SHEETS_SPREADSHEET_ID -ErrorAction SilentlyContinue
```

Then run:

```powershell
pytest -m live_sheets -v
```

The test spreadsheet must be shared with the service account email as **Editor**.

The live suite uses:

- session-scoped `GoogleSheetsStorage`
- unique `run_token` prefixes per test
- session-end cleanup of created rows
- `LIVE_SHEETS_TEST_DELAY_S` to avoid quota bursts

If `GOOGLE_SHEETS_TEST_SPREADSHEET_ID` is unset, the Sheets-parametrized tests skip.

If `GOOGLE_SHEETS_TEST_SPREADSHEET_ID` equals `GOOGLE_SHEETS_SPREADSHEET_ID`, the fixture fails loudly.

### 8.3 Google Sheets smoke script

The smoke script performs an end-to-end live storage check against the test spreadsheet.

Run:

```powershell
$env:GOOGLE_SHEETS_TEST_SPREADSHEET_ID = "PASTE_TEST_SPREADSHEET_ID_HERE"
$env:GOOGLE_SHEETS_CREDENTIALS_PATH = ".\credentials\service_account.json"
$env:LIVE_SHEETS_TEST_DELAY_S = "8"
Remove-Item Env:\GOOGLE_SHEETS_SPREADSHEET_ID -ErrorAction SilentlyContinue

python scripts/smoke_google_sheets.py
```

The script checks:

- bootstrap/header validation
- product create/read
- customer create/read
- order create/read with two order items
- `update_order_status` confirmed_at persistence
- stock movement append/list
- parse log append
- cleanup of rows created during the run

It prints `PASS` / `FAIL` per step and exits with a nonzero code if a step fails.

Run it twice consecutively to confirm that existing headers validate cleanly and that cleanup leaves the sheet reusable.

## 9. Troubleshooting

### Google Sheets API 429 quota errors

The most common live test failure is:

```text
APIError: [429]: Quota exceeded for quota metric 'Read requests'
```

Live tests and smoke scripts are intentionally slower than unit tests because Google Sheets has per-minute API quotas.

Use:

```powershell
$env:LIVE_SHEETS_TEST_DELAY_S = "8"
```

before running:

```powershell
pytest -m live_sheets -v
python scripts/smoke_google_sheets.py
```

If 429 still appears:

1. Stop rerunning immediately.
2. Wait 60–90 seconds.
3. Rerun once.
4. If it repeats, increase `LIVE_SHEETS_TEST_DELAY_S`.

`GoogleSheetsStorage` retries transient 429 and 5xx Google API failures with exponential backoff. After retry exhaustion, the error still surfaces to the caller. `LIVE_SHEETS_TEST_DELAY_S` is still recommended for live tests because it avoids unnecessary quota pressure and keeps the suite predictable.

### Spreadsheet not found

If the test fails with `SpreadsheetNotFound` or a permission-related error:

- Confirm the spreadsheet ID is copied correctly.
- Confirm the spreadsheet is shared with the service account email.
- Confirm the service account has Editor access.
- Confirm `GOOGLE_SHEETS_CREDENTIALS_PATH` points to the correct JSON file.

### Header mismatch

If initialization fails with a header mismatch:

- Check the tab name and first row in the spreadsheet.
- Compare against `src/duna_orders/storage/schema.py`.
- For a test spreadsheet, the simplest fix is usually to create a fresh blank spreadsheet and rerun bootstrap.

Do not manually edit production/client sheet headers without also checking the schema.

### Test spreadsheet accidentally equals production spreadsheet

The live fixture fails if:

```text
GOOGLE_SHEETS_TEST_SPREADSHEET_ID == GOOGLE_SHEETS_SPREADSHEET_ID
```

Use separate spreadsheets. The live suite creates and deletes test rows.

### Service account JSON missing

If credentials are not found:

```text
Google Sheets credentials file not found
```

confirm:

```powershell
Test-Path ".\credentials\service_account.json"
```

Expected:

```text
True
```

## 10. Known limitations and deferred follow-ups

### Google Sheets as pilot persistence

`GoogleSheetsStorage` now retries transient 429 and 5xx errors, but Google Sheets is still a pilot-oriented persistence backend.

This is an explicit architectural trade-off. Sheets is useful for fast validation, human inspectability, and small-client pilots, but it is not a transactional database. Long-term production use should move behind the same `StorageInterface` contract into a database-backed storage implementation.

This item is tracked as a future backend migration follow-up.

### gspread update argument order

`gspread` emits a deprecation warning for `worksheet.update(...)` argument order in:

```text
upsert_product
update_order_status
```

This is an explicit deferred cleanup item, not a blocker for the current storage validation. It should be addressed before the old argument order is removed by `gspread`.

This item is tracked as a follow-up.

### Cleanup runs at session end

Live Sheets tests clean up rows at session end using `run_token` prefixes.

This is an explicit test-hygiene trade-off. If the test process crashes before teardown, orphaned `test_run_*` rows may remain in the test spreadsheet. These rows are isolated from future tests by unique prefixes, but they may need manual cleanup.

This item is tracked as a follow-up.