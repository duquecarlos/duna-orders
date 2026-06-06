# Changelog
## M8.1 - FastAPI Twilio inbound webhook skeleton

Closed.

### Delivered

* Added a separate FastAPI webhook app under `src/duna_orders/web`.
* Added `GET /health`.
* Added `POST /webhooks/twilio/whatsapp` for Twilio WhatsApp inbound webhooks.
* Added Twilio signature validation using Twilio's `RequestValidator`.
* Validated the signature before parsing, storage access, or draft creation.
* Parsed Twilio `application/x-www-form-urlencoded` payloads.
* Extracted inbound `From` and `Body`.
* Routed inbound message text through the existing parser and draft creation path:

  * `ParsingService.parse(...)`;
  * `OrderService.create_draft(...)`.
* Created draft orders only; no auto-confirmation.
* Returned an empty `200` response for accepted webhooks so Twilio does not retry.
* Added graceful empty-body handling: signed empty messages return `200` and create no order.
* Added settings for Twilio auth token, optional public webhook URL, and configured webhook tenant.
* Added dependencies for FastAPI, Uvicorn, and Twilio.

### Verification

* `pytest tests/test_web_twilio_webhook.py -q` -> 5 passed.
* `ruff check src\duna_orders\web tests\test_web_twilio_webhook.py` -> All checks passed.

### Explicitly not included

* No outbound WhatsApp replies.
* No TwiML reply body beyond empty success response.
* No conversation state machine.
* No auto-confirmation.
* No queue or async worker.
* No new parser or LLM path.
* No `StorageInterface` changes.
* No FastAPI deployment configuration.

## M8.1C-3C - Postgres dashboard parity and query-budget assertion

Closed.

### Delivered

* Added a Postgres dashboard query-budget test using a real SQLAlchemy engine.
* Counted only SQL `SELECT` statements through SQLAlchemy `before_cursor_execute`.
* Drove the budget through the locked dashboard scenario and the same dashboard compute functions used by the Streamlit dashboard page.
* Confirmed `PostgresStorage.list_orders()` already uses `selectinload(OrderRow.items)`, so order items load through one bounded secondary `SELECT` instead of N+1 lazy loading.
* Locked the deterministic small dashboard scenario to `<= 4` SQL `SELECT` statements:
  * orders query;
  * one bounded order-items `selectinload` query;
  * customers query;
  * products query.
* Live Neon full-demo diagnostics observed `select_count: 6` for 1500 orders:
  * 1 orders query;
  * 3 bounded order-items `selectinload` batch queries;
  * 1 customers query;
  * 1 products query.
* The extra order-item queries are accepted because they are bounded `selectinload` batching, not N+1 lazy loading.
* Kept the existing Sheets read-budget test unchanged.

### Verification
* Manual Streamlit check with `DUNA_STORAGE_BACKEND=postgres`, Neon `DATABASE_URL`, and `DASHBOARD_TARGET=demo` rendered all 8 dashboard widgets with populated demo data.
* Live Neon dashboard compute diagnostic observed `select_count: 6` for the full 1500-order demo dataset.
* `pytest tests/test_postgres_dashboard_query_budget.py -q` -> 1 passed.
* `pytest tests/test_postgres_dashboard_query_budget.py tests/test_sheets_read_budget.py tests/test_dashboard_widgets.py tests/test_dashboard_page.py -q` -> 45 passed.

### Explicitly not included

* No FastAPI, Twilio, queue, conversation-state, LLM, or outbound messaging.
* No `StorageInterface` changes.
* No deletion of Sheets read-budget coverage.
* No new models or migrations.
## M8.1C-3B - Per-process Postgres engine cache

Closed.

### Delivered

* Added a framework-neutral process-level SQLAlchemy engine cache for Postgres.
* Added `get_or_create_engine(...)` keyed by `DATABASE_URL`.
* Added `get_or_create_session_factory(...)` so storage construction can reuse one session factory per cached engine.
* Added `dispose_all_engines()` and `reset_engine_cache()` for test isolation and clean shutdown hooks.
* Guarded cache check-and-create with a `threading.Lock`.
* Updated the storage factory so `DUNA_STORAGE_BACKEND=postgres` reuses the cached session factory instead of creating a new engine per `PostgresStorage` instance.
* Preserved lazy construction: building the engine/session factory/storage does not open a database connection.

### Explicitly not included

* No dashboard/Postgres parity work.
* No Postgres dashboard query-budget assertion.
* No Streamlit page changes or `st.session_state` changes.
* No `st.cache_resource`.
* No Sheets read-budget or `sheets_request_context` changes.
* No FastAPI, Twilio, queue, session lifecycle, LLM, outbound messaging, models, or migrations.
## M8.1C-3B - Per-process Postgres engine cache

Closed.

### Delivered

* Added a framework-neutral process-level SQLAlchemy engine cache for Postgres.
* Added `get_or_create_engine(...)` keyed by `DATABASE_URL`.
* Added `get_or_create_session_factory(...)` so storage construction can reuse one session factory per cached engine.
* Added `dispose_all_engines()` and `reset_engine_cache()` for test isolation and clean shutdown hooks.
* Guarded cache check-and-create with a `threading.Lock`.
* Updated the storage factory so `DUNA_STORAGE_BACKEND=postgres` reuses the cached session factory instead of creating a new engine per `PostgresStorage` instance.
* Preserved lazy construction: building the engine/session factory/storage does not open a database connection.

### Explicitly not included

* No dashboard/Postgres parity work.
* No Postgres dashboard query-budget assertion.
* No Streamlit page changes or `st.session_state` changes.
* No `st.cache_resource`.
* No Sheets read-budget or `sheets_request_context` changes.
* No FastAPI, Twilio, queue, session lifecycle, LLM, outbound messaging, models, or migrations.
## M8.1C-2 - Storage factory and Postgres backend selection

Closed.

### Delivered

* Added a UI-independent storage factory.
* Preserved existing `DUNA_STORAGE_BACKEND=memory` and `DUNA_STORAGE_BACKEND=sheets` behavior.
* Added `DUNA_STORAGE_BACKEND=postgres` to build `PostgresStorage` from `DATABASE_URL`.
* Kept the default backend as `memory`.
* Kept Postgres construction lazy; storage construction does not connect to the database.
* Updated `get_storage()` to delegate to the storage factory.
* Added factory-level tests for memory, sheets runtime/demo targets, postgres, missing `DATABASE_URL`, and invalid backend values.
* Added UI setup coverage for postgres backend selection.

### Explicitly not included

* No dashboard/Postgres parity work.
* No Streamlit page changes beyond `get_storage()` delegation.
* No runtime `sqlite` backend.
* No engine/pool lifecycle optimization.
* No webhook, Twilio, queue, session lifecycle, LLM, or outbound messaging.
## M8.1C-1B - Postgres demo reseed with bulk helpers

Closed.

### Delivered

* Added Postgres-specific bulk seeding helpers for products, customers, orders, and order items.
* Kept bulk helpers outside `StorageInterface`; they are trusted seeding/migration utilities only.
* Added tenant-scoped wipe behavior across products, customers, orders, order items, stock movements, and parse logs.
* Added a mandatory `tenant_id` guard for tenant-scoped delete operations.
* Added atomic `PostgresStorage.reseed_demo_dataset(...)` orchestration.
* Added `scripts/reseed_postgres.py` as a thin `DATABASE_URL`-driven CLI wrapper.
* Added non-live SQLite-backed reseed tests.
* Added live Neon reseed coverage behind the `live_postgres` marker.

### Verification

* `pytest tests/test_postgres_reseed.py -q`
* `pytest tests/test_postgres_reseed.py -q -m live_postgres`
* `python scripts/reseed_postgres.py`
* `pytest -q`
* `ruff check` on touched files
* `git diff --check`

### Explicitly not included

* No runtime backend selection.
* No Streamlit/Postgres wiring.
* No dashboard changes.
* No new models or migrations.
* No FastAPI webhook.
* No Twilio.
* No queue.
* No session lifecycle.
* No LLM or outbound messaging.
## M8.1C-0 - Live Postgres verification harness

Closed.

### Delivered

* Added `live_postgres` as an opt-in pytest marker.
* Kept live Postgres tests excluded from the default test run.
* Added a live Postgres smoke test for Alembic `upgrade head`.
* Added a live Postgres smoke test for the current `PostgresStorage` product, customer, and order flow.
* Documented `DATABASE_URL` in `.env.example`.
* Verified the current migration and storage layer against Neon Postgres.

### Verification

* `python -c "from duna_orders.config import settings; print('DATABASE_URL configured:', bool(settings.database_url)); print('Host/db:', settings.database_url.split('@')[-1] if settings.database_url else None)"` -> configured against Neon.
* `pytest tests/test_postgres_live_smoke.py -q` -> 2 deselected.
* `pytest tests/test_postgres_live_smoke.py -q -m live_postgres` -> 2 passed.
* `pytest tests/test_alembic_scaffold.py tests/test_postgres_storage_products_customers.py tests/test_postgres_storage_orders.py -q` -> 17 passed.
* `git diff --check` -> clean.

### Explicitly not included

* No runtime backend selection.
* No Streamlit wiring to Postgres.
* No dashboard changes.
* No demo reseeding into Postgres.
* No FastAPI webhook.
* No Twilio.
* No queue.
* No session lifecycle.
* No LLM or outbound messaging.

## M8.1A - Postgres foundation
Closed.

### Delivered

* Added SQLAlchemy 2.0 foundation.
* Added Alembic migration scaffold.
* Added `psycopg[binary]` for future Postgres connectivity.
* Added `ruff` to development dependencies.
* Added `database_url` to project settings.
* Added shared SQLAlchemy declarative `Base` with stable naming conventions.
* Added Postgres session utilities:

  * `make_engine(...)`;
  * `make_session_factory(...)`;
  * `session_scope(...)`.
* Added Alembic configuration using project settings instead of a hardcoded database URL.
* Connected Alembic autogenerate metadata to `Base.metadata`.
* Enabled Alembic comparison for column types and server defaults.
* Added scaffold tests that do not require a real Postgres server.
* Removed generated `src/duna_orders.egg-info/*` artifacts from Git tracking.
* Ignored future `*.egg-info/` generated folders.
* Removed an unrelated unused import found by the wider Ruff check.

### Verification

* `pytest tests/test_alembic_scaffold.py -q` -> 5 passed.
* `pytest tests/test_postgres_foundation.py -q` -> 4 passed.
* `pytest tests/test_storage_contract.py -q` -> 15 passed, 15 deselected.
* `alembic history` -> no revisions, no error.
* `ruff check src\duna_orders\storage alembic tests\test_alembic_scaffold.py tests\test_postgres_foundation.py` -> all checks passed.
* `git diff --check` -> clean.
* `git status --short` -> clean.

### Explicitly not included

* No SQLAlchemy table models.
* No migrations.
* No `PostgresStorage` implementation.
* No real Postgres connection.
* No runtime backend selection changes.
* No webhook.
* No Twilio.
* No queue.
* No LLM.
* No outbound messaging.

## M8.1B - Postgres runtime model parity

Closed.

### Delivered

* Added SQLAlchemy table models for the current runtime persistence entities:

  * `products`;
  * `customers`;
  * `orders`;
  * `order_items`;
  * `stock_movements`;
  * `parse_log`.
* Added the first Alembic migration:

  * `2026_06_01_1557-aec69eff0019_create_current_runtime_tables.py`.
* Added `PostgresStorage`.
* Implemented product and customer persistence methods.
* Implemented order and order-item persistence methods.
* Implemented stock movement and parse-log persistence methods.
* Added UTC-aware datetime normalization for SQLite-backed test reads.
* Added focused SQLite-backed tests for Postgres storage behavior.
* Added `PostgresStorage` to the shared storage contract fixture.
* Verified the storage contract now runs against:

  * `InMemoryStorage`;
  * `PostgresStorage`;
  * `GoogleSheetsStorage` only when `live_sheets` is enabled.

### Verification

* `pytest tests/test_postgres_models.py -q` -> 8 passed.
* `pytest tests/test_alembic_scaffold.py -q` -> 5 passed.
* `pytest tests/test_postgres_foundation.py -q` -> 4 passed.
* `pytest tests/test_postgres_storage_products_customers.py -q` -> 5 passed.
* `pytest tests/test_postgres_storage_orders.py -q` -> 7 passed.
* `pytest tests/test_postgres_storage_stock_parse.py -q` -> 6 passed.
* `pytest tests/test_storage_contract.py -q` -> 30 passed, 15 deselected.
* SQLite Alembic smoke test passed:

  * `alembic upgrade head`;
  * `alembic downgrade base`;
  * `alembic upgrade head`;
  * `alembic current` -> `aec69eff0019 (head)`.
* `ruff check` passed for the Postgres storage and migration-related files.
* `git diff --check` passed.
* `git status --short` was clean.

### Explicitly not included

* No runtime backend selection.
* No Streamlit wiring to Postgres.
* No live Postgres or Neon connection.
* No demo reseeding into Postgres.
* No FastAPI.
* No webhook.
* No Twilio.
* No queue.
* No LLM.
* No outbound messaging.

## M7.6 - Dashboard demo realism and presentation closure

Closed.

### Delivered

* Expanded demo customers from 30 to 730.
* Rebalanced demo orders into regular, medium-tail, and one-time customers.
* Replaced flat date cycling with deterministic demand-weighted daily volume.
* Added curated signature item weighting and Colombian restaurant pairings.
* Added demo reference-date behavior:

  * demo mode uses the max local order date from loaded orders;
  * runtime mode uses the real current date.
* Fixed Today’s Pulse COP truncation.
* Added today status strip.
* Improved Week Trend readability with split visuals:

  * orders line chart;
  * revenue bar chart.
* Replaced Top items this week with Top items by category.
* Replaced Status breakdown with Week over week.
* Added week-to-date versus prior week-to-date comparison using Monday-start periods.
* Added inverted cancellation-rate color semantics:

  * cancellation down = green down-arrow;
  * cancellation up = red up-arrow.
* Preserved the dashboard read-budget protection at 4 full-sheet reads.
* Kept dashboard service code free of Streamlit imports.

### Final demo dataset

* Customers: 730
* Products: 52
* Orders: 1,500
* Order items: 3,889
* Seed: 42
* Tenant: `el-fogon-colombiano`

### Current dashboard widget set

* Today’s pulse
* Week over week
* Week trend
* Time-of-day heatmap
* Customer mix
* Top customers
* Top items by category
* Items frequently ordered together

### Verification

* Dashboard widget tests and read-budget tests passed.
* `scripts/measure_sheets_reads.py` confirmed the cold-cache dashboard render remains at 4 full-sheet reads.
* Services grep confirmed no Streamlit imports/use in the service layer.
* `git diff --check` passed.
* Manual Streamlit demo verification confirmed all eight widgets render with the demo banner.

## M7.4 - Dashboard polish and M7 closure

### Delivered

- Polished `pages/3_Dashboard.py` with one page title and a concise dashboard caption.
- Grouped dashboard sections into:
  - Now;
  - This week;
  - Patterns.
- Added consistent empty-state messages across dashboard render helpers.
- Standardized dashboard formatting:
  - COP values as `COP 45.000`;
  - counts with thousand-separator dots;
  - percentages with one decimal place.
- Added friendly page-level dashboard load error handling.
- Added `tests/test_dashboard_page.py` for the dashboard load-error path.
- Preserved one `sheets_request_context(storage)` per dashboard render.
- Preserved one `run_locked_dashboard_read_scenario(...)` call per dashboard render.
- Preserved the locked four-tab read union:
  - `orders`;
  - `order_items`;
  - `customers`;
  - `products`.

### Verification

- `python -m compileall src\duna_orders\ui\dashboard_streamlit.py pages\3_Dashboard.py tests\test_dashboard_page.py` -> OK.
- `pytest tests\test_dashboard_page.py tests\test_dashboard_widgets.py tests\test_sheets_read_budget.py -v` -> 33 passed.
- `pytest tests/test_dashboard_widgets.py tests/test_sheets_read_budget.py -v` -> 32 passed.
- `python scripts/measure_sheets_reads.py` -> Pass: True, 4 full-sheet reads.
- `pytest tests/ -v -m "not live_sheets and not live_api"` -> 140 passed, 16 deselected.
- `pytest -m live_sheets -v` with `LIVE_SHEETS_TEST_DELAY_S=10` -> 15 passed, 141 deselected.
- Streamlit smoke check -> dashboard opens and all eight widgets render.
- `git diff --check` -> clean.

### Notes

- M7.4 added polish only.
- No new widgets were added.
- No dashboard scenario change was made.
- No new storage reads were added.
- No `StorageInterface`, `OrderService`, or domain Pydantic model changes were made.
- No new dashboard tabs were accessed.

## M7 - Dashboard page for read-only pilot visibility

Closed.

Completed scope:

- Implemented the full locked eight-widget dashboard:
  - today's pulse;
  - week trend;
  - status breakdown;
  - time-of-day heatmap;
  - customer mix;
  - top customers leaderboard;
  - top items this week;
  - items frequently ordered together.
- Completed M7 in four slices:
  - M7.1: dashboard skeleton and simple aggregation widgets;
  - M7.2: leaderboard widgets;
  - M7.3: analytical widgets;
  - M7.4: polish, verification, and closure docs.
- Kept dashboard computation in `src/duna_orders/services/dashboard.py`.
- Kept Streamlit rendering in `src/duna_orders/ui/dashboard_streamlit.py`.
- Kept the dashboard page in `pages/3_Dashboard.py`.
- Preserved the locked cold-cache read budget of no more than 4 full-sheet reads.
- Preserved the migration-safe service layer for future web app or bot summaries.

Final verification:

- `pytest tests/test_dashboard_widgets.py tests/test_sheets_read_budget.py -v` -> 32 passed.
- `python scripts/measure_sheets_reads.py` -> Pass: True, 4 full-sheet reads.
- `pytest tests/ -v -m "not live_sheets and not live_api"` -> 140 passed, 16 deselected.
- `pytest -m live_sheets -v` with `LIVE_SHEETS_TEST_DELAY_S=10` -> 15 passed, 141 deselected.
- Streamlit smoke check -> dashboard opens and all eight widgets render.
- `git diff --check` -> clean.

## M7.3 - Dashboard analytical widgets

### Delivered

- Added time-of-day heatmap compute objects:
  - `TimeOfDayCell`;
  - `TimeOfDayHeatmapResult`.
- Added product-pair compute objects:
  - `ProductPairEntry`;
  - `ProductPairsResult`.
- Added `compute_time_of_day_heatmap(...)`.
- Added `compute_product_pairs(...)`.
- Added deterministic tests for:
  - heatmap weekday/hour aggregation;
  - Bogotá timezone bucketing;
  - 28-day trailing heatmap window;
  - full 168-cell heatmap grid with zero cells;
  - empty heatmap input behavior;
  - product-pair counting;
  - pair tie-break behavior;
  - duplicate product deduplication inside one order;
  - canonical pair ordering;
  - pair limit and empty input behavior;
  - pair week-window filtering;
  - missing catalog product fallback.
- Added Streamlit render helpers:
  - `render_time_of_day_heatmap(...)`;
  - `render_product_pairs(...)`.
- Wired both analytical widgets into `pages/3_Dashboard.py`.
- Preserved one `sheets_request_context(storage)` per dashboard render.
- Preserved one `run_locked_dashboard_read_scenario(...)` call per dashboard render.
- Preserved the locked four-tab read union:
  - `orders`;
  - `order_items`;
  - `customers`;
  - `products`.

### Verification

- `python -m compileall src\duna_orders\services\dashboard.py src\duna_orders\ui\dashboard_streamlit.py pages\3_Dashboard.py tests\test_dashboard_widgets.py tests\test_sheets_read_budget.py scripts\measure_sheets_reads.py` -> OK.
- `pytest tests\test_dashboard_widgets.py tests\test_sheets_read_budget.py -v` -> 32 passed.
- `python scripts\measure_sheets_reads.py` -> Pass: True, 4 full-sheet reads.
- Streamlit smoke check -> dashboard page opens and renders all eight locked widgets.
- `pytest tests/ -v` -> 139 passed, 16 deselected.
- `pytest -m live_sheets -v` with `LIVE_SHEETS_TEST_DELAY_S=10` -> 15 passed, 140 deselected.
- `git diff --check` -> clean.

### Notes

- M7.3 implements only analytical widgets.
- M7.4 remains for polish, layout, labels, empty states, formatting consistency, and final M7 closure docs.
- No `StorageInterface`, `OrderService`, or domain Pydantic model changes were made.
- No new dashboard tabs were accessed.
- Altair was used only in the Streamlit render layer for the heatmap.
## M7.2 - Dashboard leaderboard widgets

### Delivered

- Added dashboard leaderboard compute objects for:
  - top customers;
  - top items this week.
- Added `compute_top_customers(...)`.
- Added `compute_top_items(...)`.
- Added deterministic tests for:
  - customer leaderboard ranking by spend;
  - customer leaderboard tie-break by customer name;
  - anonymous and unknown customers excluded;
  - customer leaderboard limit behavior;
  - customer leaderboard empty input behavior;
  - customer leaderboard week-window filtering;
  - item leaderboard ranking by quantity;
  - item leaderboard tie-break by product name;
  - missing catalog product fallback;
  - item leaderboard limit behavior;
  - item leaderboard empty input behavior;
  - item leaderboard week-window filtering.
- Added Streamlit render helpers:
  - `render_top_customers(...)`;
  - `render_top_items(...)`.
- Wired the two leaderboard widgets into `pages/3_Dashboard.py`.
- Preserved one `sheets_request_context(storage)` per dashboard render.
- Preserved one `run_locked_dashboard_read_scenario(...)` call per dashboard render.
- Preserved the locked four-tab read union:
  - `orders`;
  - `order_items`;
  - `customers`;
  - `products`.

### Verification

- `python -m compileall src\duna_orders\services\dashboard.py src\duna_orders\ui\dashboard_streamlit.py pages\3_Dashboard.py tests\test_dashboard_widgets.py tests\test_sheets_read_budget.py scripts\measure_sheets_reads.py` -> OK.
- `pytest tests\test_dashboard_widgets.py tests\test_sheets_read_budget.py -v` -> 21 passed.
- `python scripts\measure_sheets_reads.py` -> Pass: True, 4 full-sheet reads.
- Streamlit smoke check -> dashboard page opens and renders M7.1 widgets plus top customers and top items.
- `pytest -m live_sheets -v` with `LIVE_SHEETS_TEST_DELAY_S=10` -> 15 passed, 129 deselected.
- `pytest tests/ -v` -> 128 passed, 16 deselected.
- `git diff --check` -> clean.

### Notes

- M7.2 implements only leaderboard widgets.
- Time-of-day heatmap and item-pair analysis remain deferred to M7.3.
- No `StorageInterface`, `OrderService`, or domain Pydantic model changes were made.
- No new dashboard tabs were accessed.
## M7.1 - Dashboard skeleton and simple aggregation widgets

### Delivered

- Added `src/duna_orders/services/dashboard.py`.
- Added pure dashboard compute functions for:
  - today's pulse;
  - week trend;
  - status breakdown;
  - customer mix.
- Refactored `src/duna_orders/services/dashboard_read_scenario.py` so the locked scenario returns raw typed records through `DashboardScenarioResult`.
- Preserved the locked dashboard tab union:
  - `orders`;
  - `order_items`;
  - `customers`;
  - `products`.
- Added `src/duna_orders/ui/dashboard_streamlit.py` with Streamlit-native render helpers only.
- Added `pages/3_Dashboard.py`.
- Wrapped the dashboard page body in one `sheets_request_context(storage)`.
- Kept widget computation storage-independent.
- Added deterministic tests in `tests/test_dashboard_widgets.py`.
- Updated the read-budget test and measurement script for the raw-record scenario result.

### Verification

- `python -m compileall src\duna_orders\services\dashboard.py src\duna_orders\services\dashboard_read_scenario.py src\duna_orders\ui\dashboard_streamlit.py pages\3_Dashboard.py scripts\measure_sheets_reads.py tests\test_dashboard_widgets.py tests\test_sheets_read_budget.py` -> OK.
- `pytest tests\test_dashboard_widgets.py tests\test_sheets_read_budget.py -v` -> 9 passed.
- `python scripts\measure_sheets_reads.py` -> Pass: True, 4 full-sheet reads.
- Streamlit smoke check -> dashboard page opens and renders the four M7.1 widgets.
- `pytest -m live_sheets -v` with `LIVE_SHEETS_TEST_DELAY_S=10` -> 15 passed, 117 deselected.
- `pytest tests/ -v` -> 116 passed, 16 deselected.
- `git diff --check` -> clean.

### Notes

- M7.1 implements only the first four dashboard widgets.
- Time-of-day heatmap, top customers, top items this week, and item pairs remain deferred to later M7 slices.
- No `StorageInterface`, `OrderService`, or domain Pydantic model changes were made.
## M6.5.4 - Exit verification and documentation

### Delivered

- Locked the dashboard prototype scenario before M7.
- Added `src/duna_orders/services/dashboard_read_scenario.py`.
- Added `scripts/measure_sheets_reads.py`.
- Added `tests/test_sheets_read_budget.py`.
- Defined the dashboard prototype as one future Streamlit page with eight widgets:
  - today's pulse;
  - week trend;
  - status breakdown;
  - time-of-day heatmap;
  - customer mix;
  - top customers leaderboard;
  - top items this week;
  - items frequently ordered together.
- Defined the required tab union:
  - `orders`;
  - `order_items`;
  - `customers`;
  - `products`.
- Verified the cold-cache dashboard prototype read budget:
  - target: ≤4 full-sheet `get_all_records` calls;
  - measured: 4 full-sheet reads;
  - result: pass.
- No dashboard UI was implemented.
- No Streamlit page edits were made.
- No `StorageInterface`, `OrderService`, UI semantic, or Pydantic model changes were made.

### Verification

- `python -m compileall src\duna_orders\services\dashboard_read_scenario.py scripts\measure_sheets_reads.py tests\test_sheets_read_budget.py` -> OK.
- `pytest tests\test_sheets_read_budget.py -v` -> 2 passed.
- `python scripts\measure_sheets_reads.py` -> Pass: True.
- `git diff --check` -> clean.

### Notes

- This closes M6.5 as the Sheets performance / cleanup slice.
- M7 is unlocked only after the external restaurant-owner validation conversation is completed.

## M6.5.3 - Short-TTL Sheets record cache

### Delivered

- Added `src/duna_orders/storage/sheets_cache.py`.
- Added a short-TTL, process-local cache for full-tab Google Sheets records.
- Cache key is `(spreadsheet_id, sheet_name)`.
- Cache is per-`GoogleSheetsStorage` instance, not module-level.
- Tenant filtering remains outside the cache because `get_all_records` loads full tabs.
- Added 30-second TTL with injectable time source for deterministic tests.
- Updated `GoogleSheetsStorage._load_records(...)` to consult the cache.
- Preserved request-scoped precedence: active request-context records are reused before the cache is consulted.
- Added write invalidation for:
  - products on `upsert_product(...)`;
  - customers on `create_customer(...)`;
  - orders and order_items on `create_order(...)`;
  - orders on `update_order_status(...)`;
  - stock_movements on `append_stock_movement(...)`.
- Ensured failed reads are not cached.
- Ensured cache hits return safe record copies.
- Updated request-context tests to account for legitimate cross-request cache reuse.
- Added `tests/test_sheets_cache.py`.

### Verification

- `python -m compileall src\duna_orders\storage\sheets_cache.py src\duna_orders\storage\sheets.py tests\_fakes.py tests\test_sheets_cache.py tests\test_sheets_request_context.py` -> OK.
- `pytest tests\test_sheets_cache.py -v` -> 11 passed.
- `pytest tests\test_sheets_request_context.py -v` -> 6 passed.
- `pytest tests\test_sheets_read_consolidation.py -v` -> 3 passed.
- `git diff --check` -> clean.

### Notes

- No `StorageInterface` changes.
- No `OrderService`, UI semantic, or Pydantic model changes.
- Dashboard read-budget work remains deferred to M6.5.4.

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