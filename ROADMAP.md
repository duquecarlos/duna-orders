\# Roadmap



This roadmap tracks future work for Duna Orders. It is not a changelog and does not describe completed milestones.



\## Before pilot data



These items must be resolved before writing real pilot/client data to a production spreadsheet.



\### Add prompt versioning to parse logs



Add explicit parser prompt version tracking.



Expected changes:



\- Add a `PROMPT\_VERSION` constant near the parser prompt definition.

\- Add `prompt\_version` to `ParseLogEntry`.

\- Add `prompt\_version` to the `parse\_log` sheet headers.

\- Update `ParsingService` to persist the prompt version.

\- Update storage tests and live Sheets validation.

\- Apply a one-time header update to any existing test spreadsheet.



Reason:



Parser output needs auditability. If a parse result is wrong or changes after prompt edits, the stored log should show which prompt version produced it.



\## High priority



\### GoogleSheetsStorage resilience



Add a resilience layer for transient Google Sheets failures.



Scope:



\- Retry 429 and 5xx responses with exponential backoff.

\- Keep hard failures visible after retry exhaustion.

\- Avoid hiding schema/header errors behind retries.

\- Add focused tests for retryable vs non-retryable errors.



Reason:



Live tests already exposed Google Sheets read quota limits. Tests currently mitigate this with `LIVE\_SHEETS\_TEST\_DELAY\_S`, but production storage has no retry/backoff. Transient Google API failures currently surface directly to the caller.



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



\### gspread update argument order



Migrate `worksheet.update(...)` calls to the newer argument order.



Affected areas:



\- `upsert\_product`

\- `update\_order\_status`



Reason:



Current calls work but emit deprecation warnings. This should be cleaned before the old argument order is removed by `gspread`.



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

