\# Roadmap



This roadmap tracks future work for Duna Orders. It is not a changelog and does not describe completed milestones.


## High priority
### Next milestone

M6.5 - Sheets performance / cleanup slice.

Entry:
- After M6 closure follow-ups.
- Before M7 dashboard work.
- While M6 customer/order-history changes are still fresh.

Scope:
- Consolidate Sheets reads so one request-level load uses at most one `get_all_records` call per needed sheet.
- Distribute loaded records to entity hydration instead of repeatedly reading the same tabs.
- Add a short-TTL in-memory cache, approximately 30 seconds, keyed by spreadsheet, tenant, and sheet/tab.
- Invalidate the cache on writes.
- Add customer-lookup reuse within a single operator request.

Exit criteria:
- Define and verify a dashboard prototype read budget before M7.
- First target: no more than 4 full-sheet reads per Streamlit page load for the dashboard prototype scenario.
- Live Sheets verification shows materially reduced 429/rate-limit pressure compared with the current repeated-read behavior.

Reason:
M6 added customer recognition and order history, but `GoogleSheetsStorage.get_customer_order_history(...)` currently pulls all orders and filters in Python. Before adding dashboard reads in M7, the Sheets backend needs a small performance/cleanup slice to reduce repeated tab reads and quota pressure.

### Next milestone candidates after M6.5

Status: pending selection.

Possible next directions:

- Dashboard page for read-only pilot visibility.
- Customer profile editing workflow.
- Tenant defaults for parser-assisted draft creation.
- Customer analytics and segmentation.


## Recently closed
### M6 - Customer registry and repeat recognition

Closed.

Completed scope:

- Added customer auto-recognition by phone during draft creation.
- Added lightweight phone normalization for spaces and dashes.
- Added customer order history lookup through the storage contract.
- Added customer context labels for New Order and Today’s Orders.
- Added deterministic WhatsApp confirmation message generation.
- Wired parser-created drafts to use the same customer name/phone fields as manual draft creation.
- Added live Sheets coverage for customer phone lookup and customer order history.
- Added partial-confirmation repair when stock movement already exists but order status remains draft.

Deferred follow-ups:

- Optimize Google Sheets reads to reduce 429 quota risk through M6.5.
- Add customer profile editing UI.
- Add support for customer default address reuse.
- Add dashboard/read-only analytics.
- Add customer segmentation later, after pilot feedback.

### M5 - Order lifecycle and today's-orders visibility

Closed.

Completed scope:

- Added lifecycle statuses for preparation, readiness, delivery, pickup, and cancellation.
- Added service-level lifecycle transition validation through `OrderService.transition_order_status(...)`.
- Added `status_updated_at` as the lightweight latest lifecycle timestamp.
- Extended memory and Sheets storage to persist lifecycle status updates.
- Added tested today-order visibility filtering.
- Added Today’s Orders Streamlit page for active orders and lifecycle actions.
- Verified Sheets-backed lifecycle management manually.

Deferred follow-ups:

- Create a separate live-test Google Sheet and configure `GOOGLE_SHEETS_TEST_SPREADSHEET_ID`.
- Optimize Sheets read behavior to reduce 429 quota risk during Streamlit reruns.
- Add customer registry workflow after validation feedback.
- Add dashboard/read-only analytics after validation feedback.

### M4.3 - Streamlit Sheets backend wiring

Closed.

Completed scope:

- Added env-driven backend selection for Streamlit with `DUNA_STORAGE_BACKEND`.
- Wired `GoogleSheetsStorage` into the operator-facing demo.
- Kept memory backend as the default local mode.
- Made Sheets backend fail fast when required runtime configuration is missing.
- Prevented repeated catalog upserts on every Streamlit startup.
- Updated catalog seeding to use project settings from `.env`.
- Verified persistent Sheets-backed order creation, confirmation, stock movement, parse log, and restart/readback behavior.
- Fixed duplicate-product stock impact by aggregating confirmation quantities by product.

Deferred follow-ups:

- Google Sheets quota/read optimization remains a future cleanup item.
- Order lifecycle, today's-orders view, customer registry, and dashboard remain out of scope until after M4.3.

### M4.2.6 - Parser-assisted draft creation

Closed.

Completed scope:

- M4.2.6a extracted UI setup/factory logic.
- M4.2.6b integrated parser-assisted draft creation into the New Order page.
- Added realistic demo messages and parser review models.
- Added review-before-draft behavior so the operator stays in control.
- Fixed Streamlit parser availability through settings-based API key loading.
- Updated the live parser prompt for tenant-aware output.
- Added parser payload normalization for common LLM output quirks.
- Verified parser-assisted order creation and confirmation manually with `msg_002_modifications_combined` and `msg_016_informal_messy`.

Deferred follow-ups:

- Parser-assisted draft: consider tenant-level defaults for `customer_name` extraction and `packaging_fee`.
- Page split trigger: keep `pages/1_New_Order.py` as a single page until one of these is true:
  - file exceeds ~600 lines;
  - two distinct user flows live in the same file;
  - multiple developers are touching it concurrently;
  - adding a new feature requires scrolling more than twice to find the relevant section.
- Composition/page extraction remains deferred.
- Review Google Sheets live test quota/read behavior after M4.2.

### M4.2.5b - Tenant foundation

Closed.

Completed scope:

- Added `tenant_id` to tenant-scoped domain and request models.
- Added catalog-level business metadata.
- Updated Google Sheets schema, serialization, and deserialization for tenant-aware storage.
- Migrated the live test spreadsheet.
- Verified deterministic tests, live Sheets tests, demo catalog seeding, and smoke checks.


\## Medium priority



\### Dashboard page



Add a read-only Streamlit dashboard.



Possible contents:



\- today's orders

\- total sales

\- recent confirmed orders

\- low-stock products

\- recent stock movements

\- parser warnings / failed parses



Reason:



Useful for pilots and demos. It makes the system's operational value visible beyond the order-entry page.



\### Customer registry workflow



Improve customer handling beyond free-text snapshots.



Possible scope:



\- customer search by phone

\- create/select customer from the New Order page

\- default address reuse

\- last order timestamp

\- customer notes



Reason:



Current order workflow supports customer snapshots, but a pilot business may need recurring customer handling.



\## Low priority / cleanup




\### Idempotent cleanup at live test session start



Live Sheets tests currently clean up rows at session end.



Add optional session-start cleanup for rows with known test prefixes.



Reason:



If a live test process crashes before teardown, orphaned `test\_run\_\*` rows can remain in the test spreadsheet. They are isolated by unique prefixes, but start-of-session cleanup would improve hygiene.



\### Storage exception consolidation



Replace raw built-in exceptions with storage-specific exceptions.



Possible mapping:



\- duplicate IDs: `StorageDuplicateIDError(StorageError)`

\- missing IDs: `StorageNotFoundError(StorageError)`



Reason:



Current behavior intentionally matches both backends:



\- duplicate customer/order/stock movement/parse log IDs raise `ValueError`

\- unknown `order\_id` on `update\_order\_status` raises `KeyError`



This is acceptable for the MVP, but storage-specific exceptions would make service-layer error handling clearer later.



\## Future backend migration


\### Database-backed storage



Add a database backend that implements `StorageInterface`.



Possible backends:



\- SQLite for local single-client deployments

\- PostgreSQL for multi-client production deployments

\- Supabase if managed Postgres + auth becomes useful



Principle:



The migration should add a new storage backend, not rewrite services, parser logic, or UI workflow.



Expected shape:



```text

Services

→ StorageInterface

→ InMemoryStorage / GoogleSheetsStorage / FutureDatabaseStorage

