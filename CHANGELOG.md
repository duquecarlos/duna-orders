# Changelog
## M6.5.2 - Request-scoped Sheets read consolidation

### Delivered

- Added `src/duna_orders/storage/read_context.py` with an explicit `sheets_request_context(storage)` context manager.
- Implemented request-scoped read reuse through a module-level `ContextVar`.
- Updated `GoogleSheetsStorage` read methods to reuse the active `_SheetsRecordSet` across storage method calls inside one request context.
- Preserved behavior outside any request context: each public read method still creates its own operation-scoped record set.
- Wrapped the read-heavy page body in:
  - `pages/1_New_Order.py`
  - `pages/2_Orders_Today.py`
- Did not use `st.session_state` for request-scoped read reuse.
- Did not change `StorageInterface`, `OrderService`, UI semantics, or Pydantic models.
- Did not introduce cross-request caching.

### Verification

- `python -m compileall src\duna_orders\storage\read_context.py src\duna_orders\storage\sheets.py pages\1_New_Order.py pages\2_Orders_Today.py tests\test_sheets_request_context.py` -> OK.
- `pytest tests\test_sheets_request_context.py -v` -> 6 passed.
- `pytest tests\test_sheets_read_consolidation.py -v` -> 3 passed.
- `git diff --check` -> clean.

### Notes

- Nested Sheets request contexts are intentionally rejected with `RuntimeError`.
- Context teardown resets the `ContextVar`, including exception paths.
- Short-TTL cross-request caching remains deferred to M6.5.3.

## M6.5.1 - Internal Sheets read-provider consolidation

### Delivered

- Centralized Google Sheets full-tab record loading behind the private `GoogleSheetsStorage._load_records(...)` path.
- Added `_SheetsRecordSet` as an operation-scoped record loader inside `GoogleSheetsStorage`.
- Routed read-side hydration through reusable private helpers for:
  - products;
  - customers;
  - orders and order items;
  - stock movements.
- Preserved the public `StorageInterface` contract.
- Did not change OrderService, UI behavior, or Pydantic models.
- Did not add request scoping or cross-request caching; those remain deferred to later M6.5 slices.
- Added fake Sheets test infrastructure with read counters for deterministic read-pattern tests.
- Added `tests/test_sheets_read_consolidation.py`.

### Verification

- `python -m compileall src\duna_orders\storage\sheets.py tests\_fakes.py tests\test_sheets_read_consolidation.py` -> OK.
- `pytest tests\test_sheets_read_consolidation.py -v` -> 3 passed.
- `pytest tests/test_storage_contract.py -v -m "not live_sheets"` -> 15 passed, 15 deselected.
- `git diff --check` -> clean.

### Notes

- M6.5.1 only centralizes internal read loading and creates reusable fake read-count infrastructure.
- Request-scoped consolidation remains deferred to M6.5.2.
- Short-TTL cross-request caching remains deferred to M6.5.3.

## M6 - Customer registry and repeat recognition

### Delivered

- Added customer auto-recognition during draft creation.
- Added phone normalization through `normalize_customer_phone(...)`.
- Phone normalization currently:
  - strips leading/trailing whitespace;
  - removes spaces;
  - removes dashes;
  - does not perform deep international phone normalization.
- When `OrderService.create_draft(...)` receives a customer phone:
  - it looks up an existing customer by `(tenant_id, normalized_phone)`;
  - if found, it associates the order with the existing `customer_id`;
  - if not found, it creates a new customer;
  - registered customer name takes precedence over the newly typed name.
- Added `StorageInterface.get_customer_order_history(...)`.
- Implemented customer order history in both `InMemoryStorage` and `GoogleSheetsStorage`.
- Added `src/duna_orders/services/customer_context.py` for shared customer context and repeat-customer labels.
- Added customer context to the New Order page:
  - `Cliente nuevo`;
  - `Cliente conocido: [name] - [N] pedido(s) anterior(es)`;
  - note when a typed name differs from the registered customer name.
- Added customer badges to Today’s Orders:
  - `First order`;
  - `Repeat customer (N orders)`.
- Added deterministic Colombian-Spanish WhatsApp confirmation message generation in `src/duna_orders/ui/confirmation_message.py`.
- Added WhatsApp confirmation message display after order confirmation.
- Updated parser-created draft flow so customer name/phone fields are reused instead of hardcoded anonymous customer data.
- Added Sheets deserialization safeguards for numeric-looking phone values in `customer_phone` and `customer_phone_snapshot`.
- Added retry repair logic for partial confirmation cases where a deterministic sale stock movement already exists but the order status is still `draft`.

### Verification

- `python -m compileall src tests scripts pages streamlit_app.py` -> OK.
- `pytest tests/ -v -m "not live_sheets and not live_api"` -> 86 passed, 16 deselected.
- `pytest -m live_sheets -v` with `LIVE_SHEETS_TEST_DELAY_S=12` -> 15 passed, 87 deselected.
- Manual Sheets-backed Streamlit verification passed:
  - new customer phone created a customer row in Google Sheets;
  - repeated phone recognized the stored customer;
  - typed name mismatch kept the registered customer name;
  - parser-created drafts used the same customer name/phone fields;
  - WhatsApp confirmation message displayed after confirmation;
  - Today’s Orders showed customer context;
  - inconsistent draft-plus-stock-movement runtime order was repaired successfully.

### Notes

- No parser prompts were changed.
- No outbound WhatsApp messaging was added.
- No customer profile editing UI was added.
- No dashboard analytics were added.
- No multi-phone customer support was added.
- `GoogleSheetsStorage.get_customer_order_history(...)` is currently naive: it calls `list_orders()`, hydrates the full order list, and filters by `tenant_id` and `customer_id` in Python.
- Customer order history read optimization is deferred into M6.5 as part of the Sheets performance / cleanup slice.
- Google Sheets 429 quota/read pressure remains a known optimization item before dashboard work.
- `OrderService.confirm_order(...)` repairs partial-confirmation retries only when the existing sale stock movement exactly matches the expected deterministic payload.

## M5 - Order lifecycle and today's-orders visibility

### Delivered

- Extended order statuses from creation/confirmation into a simple operational lifecycle:
  - `draft`
  - `confirmed`
  - `in_preparation`
  - `ready`
  - `delivered`
  - `picked_up`
  - `cancelled`
- Added `status_updated_at` to orders as the latest lifecycle timestamp.
- Added `OrderService.transition_order_status(...)` for controlled lifecycle transitions.
- Added service-level transition validation:
  - `confirmed` -> `in_preparation`, `cancelled`
  - `in_preparation` -> `ready`, `cancelled`
  - `ready` -> `delivered`, `cancelled` for delivery orders
  - `ready` -> `picked_up`, `cancelled` for pickup orders
  - terminal states cannot transition further.
- Added tenant scoping to lifecycle transitions.
- Extended `StorageInterface.update_order_status(...)` and both storage backends to persist `status_updated_at`.
- Added `src/duna_orders/services/order_visibility.py` for testable today/order visibility filtering.
- Added `pages/2_Orders_Today.py` for active order visibility and lifecycle actions.
- Replaced the empty dashboard placeholder page with the Today’s Orders page.
- Updated live Sheets test setup so it can read `GOOGLE_SHEETS_TEST_SPREADSHEET_ID` from project settings.
- Updated `.env.example` so live test Sheet ID is blank by default and not copied from the runtime Sheet ID.
- Documented the `status_updated_at` Sheets migration in `MIGRATIONS.md`.

### Verification

- `python -m compileall src tests scripts pages streamlit_app.py` -> OK.
- `pytest tests/ -v -m "not live_sheets and not live_api"` -> 74 passed, 14 deselected.
- `pytest -m live_sheets -v` -> 13 skipped, 71 deselected because `GOOGLE_SHEETS_TEST_SPREADSHEET_ID` is intentionally blank until a separate live-test spreadsheet is created.
- Manual Sheets-backed Streamlit check passed after refreshing through a transient Google Sheets 429 quota error:
  - existing confirmed orders appeared in Today’s Orders;
  - lifecycle actions worked through preparation, ready, and delivered states;
  - completed/cancelled toggle worked.

### Notes

- No parser prompts were changed.
- No customer registry was added.
- No dashboard analytics were added.
- No status history table or audit log was added; `status_updated_at` is the current lightweight lifecycle timestamp.
- A separate live-test Google Sheet remains deferred.
- Google Sheets quota/read optimization remains a future cleanup item.
## M4.3 - Streamlit Sheets backend wiring

### Delivered

- Added `DUNA_STORAGE_BACKEND` setting with `memory` as the default backend.
- Added `GOOGLE_SHEETS_SPREADSHEET_ID` to runtime settings.
- Updated `get_storage()` so Streamlit can use either `InMemoryStorage` or `GoogleSheetsStorage`.
- Kept memory backend behavior unchanged for local demo use.
- Added fail-fast startup behavior when `DUNA_STORAGE_BACKEND=sheets` is selected without required Sheets configuration.
- Added `prepare_storage_catalog(...)` so memory storage is seeded from the demo catalog, while Sheets storage only checks whether products already exist.
- Avoided automatic product upserts on every Streamlit startup when using Sheets.
- Updated `scripts/seed_demo_catalog.py` to read the runtime Sheet ID and credentials through project settings.
- Renamed the Streamlit reset button to `Reset UI session` to clarify that it clears local UI state only.
- Fixed duplicate-product stock impact during order confirmation by aggregating quantities by `product_id`.
- Added regression coverage for duplicate product lines and aggregate insufficient-stock checks.

### Verification

- `python -m compileall scripts\seed_demo_catalog.py src\duna_orders\config.py` -> OK.
- `python scripts/seed_demo_catalog.py --dry-run` -> 52 products loaded from catalog.
- `pytest tests/test_seed_demo_catalog.py tests/test_ui_setup.py tests/test_orders_service.py -v` -> 31 passed.
- `pytest tests/ -v` -> 63 passed, 13 deselected.
- Manual memory backend check passed:
  - backend displayed as `InMemoryStorage`;
  - products loaded;
  - draft creation worked;
  - confirmation worked;
  - inventory decreased;
  - reset reseeded local memory state.
- Manual Sheets backend check passed:
  - backend displayed as `GoogleSheetsStorage`;
  - products loaded from Sheets;
  - parser-assisted draft worked;
  - confirmation worked;
  - order row appeared in `orders`;
  - item rows appeared in `order_items`;
  - stock movements appeared in `stock_movements`;
  - parse log appeared in `parse_log`;
  - restart plus `get_order(...)` verified persistence.
- Duplicate product stock impact verified manually:
  - two Bandeja paisa order item lines produced one aggregated stock movement with `quantity_delta = -2`;
  - one aguacate item produced `quantity_delta = -1`.

### Notes

- `scripts/seed_demo_catalog.py --delay-s 8` is now treated as a one-time setup/catalog-refresh command, not a normal startup command.
- Runtime Streamlit configuration is read from `.env`; `.env.example` is only the template.
- `Reset UI session` clears Streamlit session state only. It does not reset persistent Google Sheets inventory.
- No new retry/backoff infrastructure was added in M4.3.
- No order lifecycle changes, customer registry, dashboard, prompt changes, new pages, or new domain fields were added.
- M4.3 closes the persistence gap for the operator-facing demo.


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