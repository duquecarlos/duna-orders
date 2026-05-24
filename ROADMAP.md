\# Roadmap



This roadmap tracks future work for Duna Orders. It is not a changelog and does not describe completed milestones.


\## High priority
### Next milestone candidates

Status: pending selection.

Possible next directions:

- Order lifecycle / today's-orders visibility.
- Dashboard page for read-only pilot visibility.
- Customer registry workflow for recurring customer handling.
- Live Sheets test quota/read optimization.
- Tenant defaults for parser-assisted draft creation.


## Recently closed

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

