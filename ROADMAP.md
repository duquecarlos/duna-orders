# Roadmap

This roadmap tracks future work for Duna Orders and keeps a lightweight milestone archive.

Detailed completed work belongs in `CHANGELOG.md`. This file only keeps milestone-level summaries, deferred follow-ups, and next-candidate direction.

## High priority

## M8 - WhatsApp conversational ordering and Postgres runtime foundation

Status: in progress.

M8 adds WhatsApp conversational ordering and moves the runtime foundation from Google Sheets to Postgres. The milestone is both a platform-hardening milestone and the first conversational-channel milestone.

Primary goals:

- Introduce Postgres as the runtime backend.
- Preserve existing order-management and dashboard behavior on Postgres.
- Add FastAPI webhook ingestion for Twilio WhatsApp Sandbox.
- Add durable inbound idempotency and tenant-channel binding.
- Add session lifecycle for multi-turn customer conversations.
- Add Postgres-backed job processing.
- Add outbox-based outbound messaging with safety guards.
- Add structured LLM conversational turn handling.
- Allow autonomous clarification messages only after safety harness validation.
- Require operator confirmation for commitment messages.
- Prepare multi-model evaluation and future provider/channel replacement.

### M8.0 - Architecture lock

Status: closed.

Scope:

- Create `ARCHITECTURE-M8.md`.
- Update `DECISIONS.md` with locked M8 architecture decisions.
- Update `ROADMAP.md` with M8 execution route.
- No implementation code.

### M8.1A - Postgres foundation

Status: closed.

Scope completed:

* Added SQLAlchemy 2.0 foundation.
* Added Alembic migration scaffold.
* Added Postgres database URL configuration.
* Added shared SQLAlchemy metadata naming conventions.
* Added session factory and transaction-scope utilities.
* Added scaffold tests that do not require a real Postgres server.
* Removed generated `egg-info` artifacts from Git tracking.

Explicitly deferred to M8.1B:

* `PostgresStorage`.
* SQLAlchemy table models.
* First migration.
* Current domain persistence parity.
* Runtime backend selection.

Exit result:

* Storage and migration foundation exists.
* Existing storage contract tests still pass.
* No WhatsApp-specific runtime behavior exists yet.


### M8.1B - Demo/runtime model parity

Status: closed.

Scope completed:

* Added SQLAlchemy table models for current runtime persistence.
* Added the first Alembic migration for current runtime tables.
* Implemented `PostgresStorage`.
* Supported current product, customer, order, order-item, stock movement, and parse-log flows through `PostgresStorage`.
* Preserved `StorageInterface` as the persistence boundary.
* Kept services free of SQLAlchemy model dependencies.
* Added `PostgresStorage` to the default non-live storage contract suite.

Exit result:

* Current domain persistence can be represented in Postgres.
* Existing storage contract tests pass against both memory and Postgres by default.
* Sheets remains available only through the `live_sheets` marker.
* No WhatsApp-specific runtime behavior exists yet.

Explicitly deferred to later slices:

* Runtime backend selection.
* Live Postgres or Neon connection.
* Streamlit wiring to Postgres.
* Deterministic demo reseeding into Postgres.


### M8.1C - Deterministic demo reseed and dashboard parity

Status: planned.

Scope:

- Re-seed deterministic demo data fresh into Postgres.
- Preserve demo tenant `el-fogon-colombiano`.
- Preserve demo reference-date behavior.
- Verify dashboard renders from Postgres-backed data.
- Adjust dashboard assumptions for Postgres where needed.

Exit criteria:

- Demo data is reproducible from seeders.
- Dashboard works from Postgres.
- Existing locked dashboard widgets remain intact.

### M8.1D - FastAPI inbound skeleton

Status: planned.

Scope:

- Add FastAPI webhook service skeleton.
- Add `/health`.
- Add `POST /webhook/whatsapp`.
- Add Twilio signature verification.
- Add `TenantChannelBinding`.
- Add `InboundMessage`.
- Add Twilio `MessageSid` idempotency.
- Acknowledge inbound webhook quickly after persistence.

Explicitly excluded:

- Session lifecycle.
- LLM.
- Outbound.
- Real sends.

Exit criteria:

- Valid Twilio Sandbox inbound payload can be verified and persisted.
- Duplicate provider message IDs do not enqueue duplicate work.
- Unknown tenant/channel binding is logged but not processed.
- Webhook returns quickly without running conversation logic.

### M8.2 - Job queue and session lifecycle

Status: planned.

Scope:

- Add Postgres-backed `Job` table.
- Add job claim pattern using row-level locking.
- Add worker scaffolding.
- Add `Session`.
- Add append-only `ConversationEvent`.
- Resolve sessions by tenant, channel, and customer phone.
- Add optimistic session versioning.
- Add idle session expiry behavior.

Explicitly excluded:

- LLM.
- Outbound.
- Real sends.

Exit criteria:

- Inbound messages become ordered conversation events.
- Session versions prevent stale writes.
- Idle sessions can expire.
- Same-customer messages are serialized or conflict safely.

### M8.3 - Outbox, policy engine, and status callback

Status: planned.

Scope:

- Add `OutboundMessage`.
- Add `OutboundStatusEvent`.
- Add `OutboxService`.
- Add `OutboundPolicyEngine`.
- Add `ChannelDispatcher`.
- Add `MockChannelAdapter`.
- Add `POST /webhook/twilio/status`.
- Implement and test the 12 outbound safety guards.

Explicitly excluded:

- Real Twilio sends.
- LLM-driven outbound.
- Commitment sends.

Exit criteria:

- Outbound rows are persisted before any send attempt.
- Suppressed messages are logged with reasons.
- Each safety guard suppresses independently.
- Mock channel adapter cannot reach Twilio.
- Status callbacks can be recorded and safely interpreted.

### M8.4 - Structured LLM turn handler and active sessions UI

Status: planned.

Scope:

- Add `StructuredTurnClient`.
- Add Anthropic Claude Haiku adapter.
- Add `TurnOutputSchema`.
- Validate provider structured output with Pydantic.
- Add catalog snapshot/versioning.
- Add prompt caching context.
- Add malformed-output and low-confidence policies.
- Add `LLMCallLog`.
- Add active sessions operator UI.
- Add operator identity dropdown.
- Add stale-view detection.

Explicitly excluded:

- Real WhatsApp sends unless already allowed by safety harness in later slice.
- Commitment outbound.

Exit criteria:

- Bot can produce structured draft updates.
- Bot can propose clarification or operator-review actions.
- LLM errors never produce unsafe outbound.
- Active sessions are visible to the operator.
- Stale operator views cannot confirm.

### M8.5 - First real clarification sends

Status: planned.

Scope:

- Enable Twilio Sandbox real sends for allowlisted test numbers only.
- Allow clarification intents only.
- Observe status callbacks end-to-end.
- Keep commitment outbound blocked.

Exit criteria:

- Customer can send a WhatsApp message.
- Bot can ask a safe clarification question through Twilio Sandbox.
- Real sends are impossible outside the allowlist/safety harness.
- Delivery status is logged.

### M8.6 - Operator-gated commitment

Status: planned.

Scope:

- Add atomic operator confirmation transaction.
- Require configured operator identity.
- Enforce session version match.
- Create order from confirmed session draft.
- Link session to order.
- Render deterministic commitment message.
- Send commitment only after policy approval.
- Add failed-send retry flow.
- Add post-confirm amendment-session behavior.
- Add cost circuit breaker.

Exit criteria:

- Operator can confirm a session into an order.
- Commitment message is deterministic and operator-gated.
- Failed commitment sends are visible and recoverable.
- Customer corrections after confirmation do not mutate the confirmed order autonomously.
- Daily cost cap behavior is enforced.

### M8.7 - Multi-model and eval scaffolding

Status: planned.

Scope:

- Add OpenAI structured adapter.
- Add Gemini structured adapter.
- Add capability-aware structured provider interface.
- Add read-only shadow mode.
- Add eval harness skeleton using logged conversation examples.

Exit criteria:

- Alternative models can run in shadow mode without affecting customer state.
- Logged examples can be replayed for future evaluation.
- Core session/order behavior remains provider-independent.

### M8.8 - Closure and runbook

Status: planned.

Scope:

- Update README.
- Add operations runbook.
- Document Twilio Sandbox setup.
- Document ngrok local development flow.
- Document Railway deployment notes.
- Document stuck session recovery.
- Document retry procedures.
- Update CHANGELOG.
- Update ROADMAP.
- Verify required M8 test matrix.

Exit criteria:

- M8 is documented, testable, and operable.
- Claude review can be requested with the final architecture, decisions, roadmap, and verification output.

## Recently closed

### M8.5D-F - Stage 1 scoped-read caller migrations

Closed.

Completed scope:

* Migrated Orders Today from direct broad `storage.list_orders()` to `TenantScopedReadService.list_orders(tenant_id=...)`.
* Preserved Orders Today filtering, completed/cancelled toggle behavior, lifecycle actions, tenant checks, and UI layout.
* Migrated New Order parser context, manual product selector, and inventory table from direct broad `storage.list_products(...)` to tenant-scoped product reads.
* Preserved New Order `active_only` behavior, parser behavior, `PROMPT_VERSION`, draft creation semantics, and inventory display.
* Migrated runtime inbound parser product context from manual broad-read tenant filtering to `TenantScopedReadService.list_products(tenant_id=..., active_only=True)`.
* Preserved Twilio signature validation, `MessageSid` idempotency, duplicate/empty-body behavior, parsing, draft request normalization, draft creation, and processed-message linking.
* Added focused webhook coverage proving another tenant's active product is excluded from inbound parser context.

Current Stage 1 usage:

* Dashboard read scenario.
* Orders Today.
* New Order product reads.
* Runtime inbound parser product context.

Stage 2A progress:

* Added a static runtime read guard over the Stage 1 page/dashboard/runtime
  read modules.
* Named inbound review's intentional cross-tenant diagnostic order lookup as
  `get_order_for_diagnostics(...)`.
* Marked `OrderService` action/write broad order reads as deferred write-path
  broad reads.

Stage 2B-2 progress:

* Established `unscoped_` as the broad cross-tenant storage-read naming
  convention.
* Applied it to product and customer broad list reads only:
  `unscoped_list_products(...)` and `unscoped_list_customers(...)`.
* Kept scoped service APIs stable and kept no old-name aliases.

Deferred follow-ups:

* Stage 2B follow-on renames for `get_order(...)`, `list_orders(...)`, and
  `list_stock_movements(...)` when their boundaries are ready.
* Stage 3 `StorageInterface` evolution after the scoped contract is stable and callers are migrated.
* Tenant ID request-context/runtime resolution design.

### M8.5C - Tenant-scoped read proof-of-use

Closed.

Completed scope:

* Added `TenantScopedReadService` as a thin read-only layer above the unchanged `StorageInterface`.
* Required explicit keyword-only `tenant_id` for `list_orders(...)`, `get_order(...)`, `list_products(...)`, and `list_customers(...)`.
* Delegated to existing broad reads and filtered internally without adding backend-specific imports.
* Migrated only `run_locked_dashboard_read_scenario(...)` as the proof-of-use caller.
* Kept dashboard public signature, layout, and metric semantics unchanged.
* Added tenant-isolation, required-tenant, filter-preservation, memory/Postgres parity, and dashboard scenario tests.

Deferred follow-ups:

* Stage 2 broad-read quarantine.
* Stage 2 guard tests for page/dashboard/runtime broad-read usage.
* Stage 3 `StorageInterface` evolution after the scoped contract is stable and callers are migrated.
* Tenant ID request-context/runtime resolution design.

### M8.5A - Postgres storage hardening

Closed.

Completed scope:

* Inspected Postgres storage parity and hardening gaps for the inbound review and atomic confirmation runtime path.
* Confirmed current `StorageInterface` parity for Postgres and kept Postgres-only processed-message, lifecycle, atomic-confirmation, and bulk/demo capabilities outside the interface.
* Hardened duplicate sale movement flush conflicts so atomic confirmation maps them to `DuplicateStockMovementError` and rolls back.
* Documented processed-message linking behavior with tests for message-SID-keyed `mark_order_created(...)` and tenant-scoped reads.

Deferred follow-ups:

* Broad tenant-scoped storage reads remain a future architecture issue.
* Future multi-tenant hardening may need tenant-scoped read services or `StorageInterface` evolution.
* Claude review is recommended before implementing tenant-scoped broad-read changes.

### M8.4 - Inbound review operator hardening

Closed.

Completed scope:

* Hardened inbound review list-load and action errors so operators see mapped, actionable messages instead of raw exception text.
* Added a service-level inbound review snapshot for draft items, approved items, and linked-message diagnostics.
* Surfaced safe aggregate diagnostics for linked processed messages skipped because their orders are missing, tenant-mismatched, confirmed, cancelled, or otherwise non-reviewable.
* Kept draft review and approved confirmation queues separate in the operator UI.

Deferred follow-ups:

* Unlinked/no-result processed-message diagnostics.
* Parse-failure inbox behavior.
* Parse-log, timestamp proximity, and reparse behavior.
* Inbound media/comprobante handling.
* Outbound/customer messaging.
* Payment-status enforcement.

### M7.6 - Dashboard demo realism and closure

Closed.

Completed scope:

* Added a realistic seeded demo dataset for El Fogón Colombiano.
* Expanded demo customers to support long-tail and one-time customer behavior.
* Improved order generation with deterministic demand-weighted daily rhythm.
* Improved item generation with curated Colombian restaurant pairings.
* Added evergreen demo reference-date behavior.
* Polished dashboard presentation for demo usage.
* Updated the locked dashboard widget set to the current 8 widgets:

  * Today’s pulse;
  * Week over week;
  * Week trend;
  * Time-of-day heatmap;
  * Customer mix;
  * Top customers;
  * Top items by category;
  * Items frequently ordered together.
* Preserved the cold-cache dashboard read budget at 4 full-sheet reads.

Verification:

* Focused dashboard tests passed.
* Read-budget test passed.
* Manual Streamlit demo check passed.
* Services remained UI-free.

Deferred follow-ups:

* M8 real WhatsApp bot integration planning.
* External restaurant-owner validation remains deferred until after M8.
* Further dashboard improvements should be driven by pilot or validation feedback.

### M7 - Dashboard page for read-only pilot visibility

Closed.

Completed scope:

- Added the read-only Streamlit dashboard page.
- Implemented the locked eight-widget dashboard:
  - today's pulse;
  - week trend;
  - status breakdown;
  - time-of-day heatmap;
  - customer mix;
  - top customers leaderboard;
  - top items this week;
  - items frequently ordered together.
- Kept dashboard compute logic in `src/duna_orders/services/dashboard.py`.
- Kept dashboard rendering in `src/duna_orders/ui/dashboard_streamlit.py`.
- Kept the dashboard page wrapped in a single `sheets_request_context(storage)`.
- Preserved one locked scenario call per dashboard render.
- Preserved the four-tab read union:
  - `orders`;
  - `order_items`;
  - `customers`;
  - `products`.
- Verified cold-cache dashboard read budget remains at 4 full-sheet reads.

Verification:

- `python scripts/measure_sheets_reads.py` -> Pass: True, 4 full-sheet reads.
- `pytest tests/ -v -m "not live_sheets and not live_api"` -> 140 passed, 16 deselected.
- `pytest -m live_sheets -v` with `LIVE_SHEETS_TEST_DELAY_S=10` -> 15 passed, 141 deselected.
- Streamlit smoke check -> dashboard opens and all eight widgets render.

Deferred follow-ups:

- M8 real WhatsApp bot integration planning.
- External restaurant-owner validation conversation remains deferred until after M8.
- Dashboard visual polish beyond simple M7.4 grouping remains deferred until pilot feedback.


### M6.5 - Sheets performance / cleanup slice

Closed.

Completed scope:

- Centralized full-tab Google Sheets record loading behind a private storage path.
- Added operation-scoped record sets.
- Added request-scoped read consolidation with explicit `sheets_request_context(storage)`.
- Wrapped read-heavy Streamlit page bodies with the request context.
- Added a 30-second, per-storage-instance, short-TTL record cache.
- Added write invalidation for products, customers, orders, order_items, and stock_movements.
- Added deterministic read-count tests with fake worksheets.
- Locked the dashboard prototype scenario for M7.
- Verified the locked dashboard prototype can compute all eight widgets from four full-tab reads.
- Added `scripts/measure_sheets_reads.py`.

Verification:

- Cold-cache locked dashboard scenario reads:
  - `orders`: 1
  - `order_items`: 1
  - `customers`: 1
  - `products`: 1
  - total: 4
- Target: ≤4 full-sheet reads.
- Result: pass.

Deferred follow-ups:

- Dashboard UI was implemented and closed in M7.
- External restaurant-owner validation conversation remains deferred until after M8.
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

