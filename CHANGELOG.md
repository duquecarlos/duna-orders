# Changelog
## M4.2.6b - Parser-assisted draft creation

### Delivered

- Added realistic demo WhatsApp messages in `data/demo_messages.json`.
- Added `src/duna_orders/demo_messages.py` for loading and validating demo messages.
- Added parser review UI support in `src/duna_orders/ui/parser_review.py`.
- Added `DraftCandidate` and `DraftCandidateItem` review models.
- Added `parsed_result_to_draft_candidate(...)` to convert parser output into reviewable draft candidates.
- Integrated parser-assisted draft creation into `pages/1_New_Order.py`.
- Added a demo message selector and parser button to the New Order page.
- Added cached parser calls keyed by message text and `PROMPT_VERSION`.
- Added operator review before draft creation.
- Kept manual product picker and manual draft creation unchanged.
- Fixed parser availability in Streamlit by reading `settings.anthropic_api_key` instead of direct `os.getenv("ANTHROPIC_API_KEY")`.
- Updated the Anthropic prompt so live parser output includes `tenant_id`.
- Bumped `PROMPT_VERSION`.
- Added parser payload normalization for common LLM quirks:
  - mixed-case payment methods;
  - fulfillment aliases;
  - leading/trailing whitespace;
  - empty optional string fields;
  - item product/modification whitespace cleanup.
- Preserved `customer_name=""` because `DraftOrderRequest.customer_name` is currently required as a string.

### Verification

- `python -m compileall src tests scripts pages streamlit_app.py` -> OK.
- `pytest tests/ -v` -> 54 passed, 13 deselected.
- `pytest -m live_api -v` -> 1 passed, 66 deselected.
- `pytest -m live_sheets -v` -> reached Google Sheets; latest run got 9 passed, 3 failed, 55 deselected due external Google Sheets API 429 read quota.
- Manual Streamlit check without API key passed:
  - demo selector populated message;
  - parser warning displayed;
  - manual draft creation worked;
  - manual confirmation worked;
  - inventory decreased.
- Manual Streamlit check with API key passed for `msg_002_modifications_combined`:
  - parser panel rendered;
  - quantity edit worked;
  - draft creation from parser worked;
  - order confirmation worked;
  - inventory decreased correctly.
- Manual Streamlit check with API key passed for `msg_016_informal_messy`:
  - parser panel rendered;
  - draft creation from parser worked;
  - order confirmation worked;
  - inventory decreased correctly.

### Notes

- Live Sheets failures during final close were caused by Google Sheets API 429 read quota, not assertion failures or parser-assisted draft regressions.
- The parser interpretation of “dos bandejas paisas, una sin chicharrón y la otra con extra aguacate” as two Bandeja paisa items plus a separate Porción de aguacate is acceptable for order management because it improves pricing and stock impact accuracy.
- For messy informal messages, the parser produced a usable draft, but address/location text may still land in inferred notes instead of a dedicated delivery field.
- `customer_name=""` and `packaging_fee=0` remain acceptable for M4.2.6b and are tracked as follow-up work.
- `pages/1_New_Order.py` remains a single page for now. Composition/page extraction is deferred until the documented split triggers are reached.
- M4.2.6b is now closed.
- M4.2 is now closed.

## M4.2.6a — UI factory extraction

### Changed

- Added `src/duna_orders/ui/setup.py` for UI composition factories.
- Added `get_storage()` for current UI storage backend construction.
- Added `get_order_service(storage)` for `OrderService` wiring.
- Added `get_parsing_service(storage)` for optional parser-service wiring when `ANTHROPIC_API_KEY` is set.
- Added cached `get_demo_catalog()` for validated demo catalog loading.
- Added `seed_inmemory_from_catalog(storage, catalog)` for idempotent in-memory catalog seeding.
- Refactored `pages/1_New_Order.py` to use UI setup factories instead of inline setup logic.

### Verification

- `python -m compileall src tests scripts pages streamlit_app.py` → OK.
- `pytest tests/ -v` → 42 passed, 13 deselected.
- `pytest -m live_sheets -v` → 12 passed, 43 deselected.
- Manual Streamlit check passed: New Order page renders, catalog loads, draft creates, order confirms, and inventory decreases.

### Notes

- No new UI behavior was added.
- No parser-assisted draft creation was added.
- No storage backend switch was implemented.
- M4.2.6b remains the next slice.

## M4.2.5b — Tenant foundation closed

### Delivered

- Added required `tenant_id` to tenant-scoped domain and request models.
- Propagated `tenant_id` through `OrderService` and `ParsingService`.
- Kept tenant selection outside the parser; the parser does not infer tenant identity from customer message text.
- Added catalog-level business metadata using a top-level `business` block.
- Kept catalog products tenant-less in the JSON file and injected `business.tenant_id` when loading products.
- Updated Google Sheets headers for `products`, `customers`, `orders`, `order_items`, `stock_movements`, and `parse_log`.
- Placed `tenant_id` as column B / position 2 on all six tenant-scoped tabs.
- Updated `GoogleSheetsStorage` serialization and deserialization for tenant-aware entities.
- Updated the Google Sheets smoke script to construct tenant-aware entities.
- Manually migrated the live test spreadsheet headers.
- Seeded the demo catalog into the migrated live test spreadsheet.

### Verification

- `python -m compileall src tests scripts pages streamlit_app.py` → OK.
- `pytest tests/ -v` → 36 passed, 13 deselected.
- `pytest -m live_sheets -v` → 12 passed, 37 deselected.
- `python scripts/seed_demo_catalog.py --delay-s 8` → 52 products upserted.
- `python scripts/smoke_google_sheets.py` → All smoke checks passed.

### Notes

- Initial demo catalog seeding with `--delay-s 2` hit Google Sheets APIError 429 read quota.
- No retry/backoff infrastructure was added in M4.2.5b-E because it was out of scope.
- Rerunning the idempotent seed script with `--delay-s 8` succeeded.
- M4.2.5b is now closed.
- Next milestone: M4.2.6 parser-assisted draft creation.

## M4.2.5b-D — Google Sheets tenant schema preparation

### Changed

- Added `tenant_id` to Google Sheets schema headers for `products`, `customers`, `orders`, `order_items`, `stock_movements`, and `parse_log`.
- Placed `tenant_id` as the second column on every tab, immediately after the primary ID column.
- Updated `GoogleSheetsStorage` serialization and deserialization for tenant-aware entities.
- Updated the Google Sheets smoke script to construct tenant-aware entities using `el-fogon-colombiano`.

### Migration

- Documented the manual Google Sheets header migration in `MIGRATIONS.md`.
- Documented the expected D/E transition state where bootstrap validation rejects spreadsheets without `tenant_id` columns.
- No automated migration tooling was added.

### Known transition state

- `pytest -m live_sheets -v` remains expected to fail until M4.2.5b-E because the live test spreadsheet has not been manually migrated yet.

### Verified

- `python -m compileall src tests scripts pages streamlit_app.py` → OK.
- `pytest tests/ -v` → 36 passed, 13 deselected.

### Changed
- M4.2.5b-B: added required `tenant_id` to tenant-scoped domain/request models and propagated it through `OrderService` and `ParsingService`.
- Added shared `DEFAULT_TEST_TENANT_ID` for deterministic tests.
- Updated order, storage-contract, parsing-service, and parser fake tests for tenant-aware in-memory behavior.

### Added
- Added `ARCHITECTURE.md` documenting the customer/operator/owner product vision, tenant identity decision, current architecture, deferred work, and Phase 5 open questions.

### Known transition state
- Live Google Sheets tests are expected to fail until M4.2.5b-D/E because the Sheets schema does not yet include the required `tenant_id` columns.

### Verified
- `python -m compileall src tests scripts pages streamlit_app.py`
- `pytest tests/test_orders_service.py tests/test_storage_contract.py tests/test_parsing_service.py -v` → 30 passed, 12 deselected.

## M4.1 — Google Sheets storage resilience
### Delivered

- Added a central `_run_gspread(...)` execution boundary in `GoogleSheetsStorage`.
- Added retry handling for transient Google Sheets API failures:
  - HTTP 429 quota errors
  - HTTP 5xx server errors
- Kept non-transient errors non-retryable:
  - storage configuration errors
  - authentication errors
  - schema/header mismatches
  - duplicate-ID contract errors
  - missing-ID contract errors
- Routed Sheets reads, writes, updates, worksheet lookups, and bootstrap API calls through the retry boundary.
- Migrated `worksheet.update(...)` calls to the current `gspread` argument order.

### Added
- M4.2 Step 1: extended the demo order flow domain model with Colombian restaurant fields for fulfillment, payment, delivery zone, packaging fee, customer notes, product availability days, and item modifications.
- Added Google Sheets schema support and serialization/deserialization for the new product, order, and order item fields.
- Documented the required M4.2 Google Sheets header migration in `MIGRATIONS.md`.

### Changed
- Updated `OrderService.create_draft` to carry item modifications and fulfillment/payment metadata into draft orders.
- Updated order total calculation to include `packaging_fee` in addition to subtotal and delivery fee.
- Updated storage contract tests, order service tests, and Google Sheets smoke checks for the new fields.

### Verified
- `python -m compileall src tests scripts`
- `pytest tests/ -v` → 30 passed, 13 deselected.
- Manual test spreadsheet header migration completed.
- `pytest -m live_sheets -v` → 12 passed, 33 deselected.
- `python scripts/smoke_google_sheets.py` → All smoke checks passed.

### Verified

- `python -m compileall src tests scripts` → OK.
- `pytest tests/ -v` → passed.
- `pytest -m live_sheets -v` → passed.
- `python scripts/smoke_google_sheets.py` → passed.

### Notes

Retry/backoff improves resilience against transient Google API failures, but it does not turn Google Sheets into a transactional backend. Database-backed storage remains the long-term migration path through `StorageInterface`.

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

### Added
- M4.2.3: added the full 52-item demo restaurant catalog for `El Fogón Colombiano`.
- Added `DemoCatalogFile` validation and `load_demo_catalog()` for fail-fast demo catalog loading.
- Added deterministic catalog tests for product count, category distribution, restricted weekday availability, and parrilla weight variants.

### Verified
- `python -m compileall src tests scripts`
- `pytest tests/ -v` → 33 passed, 13 deselected.

### Added
- M4.2.4: added a products-only idempotent demo catalog seed script for Google Sheets.
- Added deterministic seed helper tests covering full catalog upsert behavior and dry-run behavior.
- Added configurable per-product delay for safer Google Sheets seeding under API quota limits.

### Verified
- `python -m compileall src tests scripts`
- `pytest tests/ -v` → 35 passed, 13 deselected.
- `python scripts/seed_demo_catalog.py --dry-run` → 52 products loaded.
- `python scripts/seed_demo_catalog.py --delay-s 2` → 52 products upserted.
- `pytest -m live_sheets -v` → 12 live tests passed, but teardown cleanup failed with Google Sheets API 429 read quota. Manual cleanup may be needed for temporary `test_run_...` rows.