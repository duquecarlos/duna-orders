# Architectural Decisions
## M8 - WhatsApp conversational ordering and Postgres runtime foundation

Status: locked for M8.0.

M8 introduces WhatsApp conversational ordering and moves the runtime storage foundation from Google Sheets to Postgres. This is a platform-hardening milestone, not only a channel integration.

### Decision: M8 starts with Postgres before WhatsApp behavior

Duna Orders will introduce Postgres as the runtime backend before enabling WhatsApp conversation logic.

Reason:

* Conversational ordering requires transactions, idempotency, queueing, session versioning, outbox semantics, delivery status callbacks, and safe operator confirmation.
* Google Sheets remains useful for early visibility and previous pilot workflows, but it should not own concurrent conversational state.
* StorageInterface remains the boundary, so the storage backend can change without rewriting the service layer.

Implementation direction:

* Introduce `PostgresStorage`.
* Keep `InMemoryStorage` for deterministic unit tests.
* Keep `GoogleSheetsStorage` as historical/legacy backend, not M8 runtime target.
* Re-seed deterministic demo data fresh into Postgres instead of migrating rows from Sheets.

### Decision: M8 keeps channels replaceable

WhatsApp is the first conversational channel, but not the product itself.

The product remains the order engine:

* tenant resolution;
* conversation session state;
* draft order creation;
* operator confirmation;
* order persistence;
* outbound policy;
* telemetry.

Twilio WhatsApp Sandbox is the M8 provider. Future Meta direct WhatsApp, Telegram, or other channels must plug in through channel adapters without changing the core services.

### Decision: FastAPI handles webhooks; Streamlit remains operator UI

M8 adds a FastAPI webhook service for inbound provider callbacks and status callbacks.

FastAPI responsibilities:

* receive Twilio inbound webhook;
* verify Twilio signature;
* resolve tenant-channel binding;
* persist inbound message;
* enqueue processing job;
* expose outbound status callback endpoint;
* run background conversation and outbound dispatcher loops for the M8 pilot.

Streamlit remains the operator-facing UI.

Streamlit is not a webhook server and is not treated as a real-time chat surface. It is a polling operator control panel.

### Decision: webhook acknowledgement is separated from conversation processing

Inbound webhooks must return quickly.

The webhook endpoint performs only:

1. signature verification;
2. tenant-channel binding lookup;
3. inbound message persistence;
4. idempotency handling;
5. job enqueue;
6. HTTP 200 response.

LLM calls, session updates, and outbound decisions happen after acknowledgement through background job processing.

Reason:

* A slow LLM call must not cause Twilio retries.
* Message ingestion must be durable before conversation processing starts.
* Duplicate inbound provider events must not create duplicate sessions or turns.

### Decision: Postgres-as-queue is acceptable for M8 pilot

M8 uses a `Job` table as a Postgres-backed queue.

Jobs are claimed with row-level locking and `SKIP LOCKED`.

Reason:

* Avoid introducing an external queue before the product needs it.
* Keep the pilot deployable with Railway + Neon.
* Preserve durability and retry behavior using the same database.

Limitation:

* M8 assumes a single FastAPI service instance for worker partitioning correctness.
* Multi-instance horizontal scaling requires stronger Postgres coordination or an external queue and is deferred post-M8.

### Decision: session processing uses optimistic versioning

Sessions use a monotonic `version`.

A worker reads session version `N`, processes the turn, and writes only if the current version is still `N`.

On conflict:

1. reload latest session state;
2. retry once;
3. on second conflict, mark the session failed or requiring operator review.

Reason:

* Customers may send messages quickly.
* Operator confirmation may race with inbound customer messages.
* Versioning prevents stale writes and stale confirmations.

### Decision: session status represents business state, not worker state

The persisted `Session.status` values for M8 are:

* `open`
* `awaiting_operator`
* `confirmed`
* `cancelled`
* `expired`
* `failed`

`processing` is not persisted as a session business status in M8.

Reason:

* Processing state belongs to the `Job` table.
* Persisted `processing` can become stale if a worker crashes.
* Session status should describe the business lifecycle of the conversation.

### Decision: conversation history is append-only

M8 stores conversation history as `ConversationEvent` rows, not as one growing JSON blob inside `Session`.

Reason:

* Easier replay/debugging.
* Safer concurrency.
* Better auditability.
* Easier future analytics and eval harness creation.

`Session` stores only the latest materialized snapshot, including current draft and version.

### Decision: outbound uses an outbox pattern

Outbound messages must be persisted before any send attempt.

Flow:

1. create `OutboundMessage`;
2. enqueue outbound dispatch job;
3. policy engine evaluates guards;
4. suppressed messages are stored with reason;
5. approved messages are sent through channel adapter;
6. provider status callbacks update delivery state.

Reason:

* Prevents real customer messages from being sent without a durable internal record.
* Makes retries and suppressed messages visible.
* Keeps sending behavior auditable.

### Decision: outbound policy is separate from channel dispatch

M8 separates:

* `OutboxService`
* `OutboundPolicyEngine`
* `ChannelDispatcher`
* `StatusCallbackHandler`
* `ChannelAdapter`

Reason:

* Business safety rules should not live inside Twilio-specific code.
* Provider adapters should only translate between Duna messages and provider APIs.
* Safety guards must be independently testable.

### Decision: outbound is blocked by default

Outbound defaults are safe in all environments.

Default posture:

* outbound disabled;
* log-only mode;
* non-production allowlist required;
* commitment messages require operator identity and exact session version match.

The policy engine evaluates 12 guards in order:

1. tenant channel enabled;
2. environment binding matches;
3. kill switch;
4. mode check;
5. allowlist;
6. session status allows outbound;
7. idempotency;
8. WhatsApp window or template;
9. rate limit;
10. length and basic content;
11. commitment requires operator;
12. opt-out list.

Each guard must be independently testable.

### Decision: outbound intent is explicit

M8 uses an `OutboundIntent` taxonomy:

* `CLARIFY_MISSING_INFO`
* `CLARIFY_SUBSTITUTION`
* `ACKNOWLEDGE_RECEIPT`
* `OPERATOR_REVIEW_NOTICE`
* `COMMITMENT_CONFIRMATION`
* `FAILURE_OR_HANDOFF`
* `PAYMENT_REQUEST`

`PAYMENT_REQUEST` is deferred and inactive in M8.

The LLM may propose an intent, but the policy engine validates whether that intent is allowed.

### Decision: commitment outbound is always operator-gated

The bot can ask clarification questions autonomously, subject to safety policy.

The bot cannot confirm an order by itself in M8.

Commitment flow requires:

* session status `awaiting_operator`;
* operator identity from configured pool;
* current session version match;
* atomic order creation;
* deterministic commitment message rendering;
* outbound policy approval.

The LLM never writes the final commitment message.

### Decision: M8 uses structured LLM output

M8 introduces a capability-aware `StructuredTurnClient`.

The initial provider is Anthropic Claude Haiku 4.5.

Provider-native structured outputs are required where available, followed by local validation.

The LLM output must include:

* action;
* draft patch;
* draft completeness;
* catalog resolution;
* next question when needed;
* operator summary;
* confidence;
* safety flags.

Malformed or low-confidence output escalates to operator review instead of sending unsafe outbound messages.

### Decision: catalog context is versioned

Every LLM turn records the catalog snapshot used.

Catalog context includes:

* tenant id;
* catalog snapshot id;
* product count;
* generated timestamp;
* catalog hash;
* prompt cache key.

Reason:

* Parser behavior must be debuggable.
* If the bot offers an unavailable or stale item, the exact catalog context can be inspected.

### Decision: operator identity is lightweight for M8

M8 uses an honor-system operator identity pool configured for the pilot.

This is not real authentication.

Reason:

* Good enough for a known-operator pilot.
* Avoids blocking M8 on full auth.
* Real authentication is deferred until the operator UI moves beyond Streamlit or requires broader access.

### Decision: operator confirmation is atomic

Order confirmation happens inside one Postgres transaction.

The transaction includes:

* create order;
* link session to order;
* advance session status;
* bump session version;
* create commitment outbound row;
* append operator action.

If the commitment send later fails, the order remains confirmed and the failed outbound is surfaced for operator retry.

### Decision: PII is intentionally constrained

M8 stores operationally necessary customer and conversation data, but avoids unrestricted raw logging.

Policy:

* phones normalized to E.164;
* phones masked by default in UI;
* raw provider payloads environment-gated;
* production prompt logs store hashes and structured variables, not full prompt text by default;
* delivery addresses masked in listings and visible in detail views;
* operator actions retained as audit trail.

### Decision: Railway + Neon are the M8 pilot deployment targets

M8 pilot deployment target:

* Railway for FastAPI webhook service;
* Railway for Streamlit operator UI;
* Neon Postgres for database.

ngrok is for local development only.

Reason:

* Low operational overhead.
* Public HTTPS endpoint for webhooks.
* Simple enough for pilot.
* Does not require AWS-level infrastructure before validation.

### Decision: cost circuit breaker protects against runaway loops

M8 defines a per-tenant daily LLM cost cap.

Default pilot cap:

* 2 USD per tenant per day.

At 80%:

* operator UI warning.

At 100%:

* autonomous outbound disabled;
* new LLM calls disabled or downgraded to operator-review-only mode;
* affected sessions surfaced as cost-paused or needing operator attention.

Reason:

* Prevent runaway LLM loops.
* Bound pilot risk.
* Detect prompt-injection or retry failures early.

### Decision: M8 slicing starts with storage foundation

M8 implementation order:

1. lock architecture;
2. introduce Postgres foundation;
3. preserve existing runtime/demo behavior on Postgres;
4. add webhook inbound;
5. add queue/session lifecycle;
6. add outbox and safety harness;
7. add structured LLM turn handling;
8. enable first real clarification sends;
9. enable operator-gated commitment;
10. add multi-model/eval scaffolding;
11. close with runbook and docs.

WhatsApp behavior does not start until the transactional foundation is stable.

## M6 — Customer recognition is service-owned and phone-based

Decision:
Use phone number as the first customer-recognition key for the MVP. `OrderService.create_draft(...)` owns customer association: it normalizes the submitted phone, looks up an existing customer by `(tenant_id, phone)`, creates a customer when none exists, and stores the associated `customer_id` on the order.

Why:
- Operators already have customer phone numbers from WhatsApp, even without a WhatsApp API integration.
- Customer recognition must work for both manual orders and parser-assisted orders.
- Keeping the logic in the service prevents Streamlit pages from duplicating customer matching rules.
- The storage backends remain responsible for persistence and lookup only.

Trade-off:
Phone-only matching is intentionally simple. It does not support multiple phones per customer, deep international normalization, or customer profile merging. Those can be added after real pilot feedback.

## M6 — Confirmation retry repairs partial stock application

Decision:
Keep deterministic sale movement IDs and make `OrderService.confirm_order(...)` repair a partial confirmation when a sale movement already exists but the order status is still `draft`.

Why:
- Google Sheets is not transactional.
- A confirm operation can append stock movement and update product stock, then fail before updating order status because of quota or network issues.
- On retry, the service should not apply stock twice.
- Existing deterministic movement IDs allow the service to detect already-applied stock impact and continue to the status update.

Trade-off:
This repair handles the known partial-confirmation case but does not replace the need for future Sheets read/write optimization or a more transactional backend.

## M1.1 — Data contract layer

Decision:
Define stable domain models, ID conventions, and spreadsheet schema before writing services or Google Sheets logic.

Why:
- The app needs one internal data shape independent of Streamlit, Google Sheets, and LLM providers.
- Stable IDs avoid coupling the system to spreadsheet row numbers.
- Snapshot fields on orders and order items preserve historical accuracy even if catalog/customer data changes later.
- Append-only stock movements make inventory auditable instead of only storing the final stock number.

Trade-off:
This adds a little structure upfront, but prevents the MVP from becoming a fragile Streamlit script.

## M1.2 — Storage contract and in-memory backend

Decision:
Define a StorageInterface and implement InMemoryStorage before connecting Google Sheets.

Why:
- Services should depend on a storage contract, not directly on Google Sheets.
- InMemoryStorage lets us build and test order/inventory logic without credentials, internet, or spreadsheet side effects.
- Returning deep copies prevents hidden mutation bugs while developing services.

Trade-off:
This adds a small abstraction early, but keeps the MVP testable and makes the future Sheets/Postgres migration cleaner.

## M1.3 — confirm_order as the first service method

Decision:
Implement OrderService with confirm_order as its single public method.
No draft creation service yet, no separate validator service.

Why:
- confirm_order is where the system's hardest invariants live: state
  transition, stock validation, audit-log append. Build it first, in
  isolation.
- Draft creation today is a passthrough to storage.create_order; wrapping
  it adds no value until validation/parsing enters the picture.
- Validation belongs inside confirm_order as a precondition, not as
  a separate service.

Failure model (deliberate):
- Order of operations: validate → append stock movements → update product
  stock cache → update order status LAST.
- If anything fails mid-flow, the order stays "draft" and the operation
  is safely retryable.
- Stock movement IDs are deterministic per (order_id, product_id) so the
  M1.2 duplicate-id guard makes retries idempotent.

Stock policy:
- Strict at confirm time: raise InsufficientStockError if any item exceeds
  current stock. The UI can offer remediation. Easy to relax later.

Trade-off:
We accept that without DB transactions, partial-failure recovery relies
on idempotent retries and deterministic IDs. Documented here so the
Sheets implementation later doesn't try to invent its own scheme.

### M1.3 — Note on field naming

During M1.3, four fields were renamed for readability:
- stock_current → current_stock
- qty → quantity
- qty_delta → quantity_delta
- (reverted) related_order_id → reference_id, kept generic to support
  future restock and adjustment movement types.

Renames retroactively updated M1.1 and M1.2 files. Future milestones should
keep retroactive renames in their own commit, separate from new logic.

### M1.3 — Note on enum scope discipline

ORDER_STATUSES and STOCK_REASONS were trimmed back to four values each
after expanding to six during implementation. The added values
("reviewed", "prepared", "manual_adjustment", "correction",
"cancelled_order_reversal") had no consuming code and risked confusing
the UI and the storage migration later. Going forward, every enum
addition must ship alongside the code that uses it.

### M1.4 — Note on new_id signature

new_id now accepts ID prefixes directly (e.g., new_id("prd"), new_id("ord"))
instead of entity names ("product", "order"). All call sites use prefixes.
Tests and services updated in sync.

## M1.5 — Draft creation as a service method

Decision:
Move draft order construction from the Streamlit page (M1.4 shortcut) into
OrderService.create_draft, with DraftOrderRequest as the typed input contract.

Why:
- Removes the temporary UI-touching-storage call from M1.4 before more
  callers (parser, future pages) replicate the pattern.
- DraftOrderRequest is the same shape the LLM parser will produce in M2.
  Defining it now means M2 is a clean handoff: parser produces request,
  service consumes it, UI passes it through.
- Service owns ID generation, snapshot resolution, and total computation.
  UI only collects user input.

Validation policy (intentional):
- create_draft validates: at least one item with qty > 0; each product
  exists; each product is active.
- create_draft does NOT validate stock. Stock validation remains the
  responsibility of confirm_order, where it has been since M1.3.
  Drafts can over-promise; confirms cannot.

Trade-off:
DraftOrderRequest adds one small model, but it becomes the canonical
contract for "what an order looks like before persistence" — usable by
both the manual UI and the future parser without translation.

## M5 — Lifecycle transitions belong in the service layer

Decision:
Add order lifecycle statuses and expose status movement through `OrderService.transition_order_status(...)`, not through direct UI or storage rules.

Why:
- Streamlit pages should render allowed actions, not define business rules.
- Storage should persist status changes, not decide whether a transition is valid.
- The same transition matrix must work for both `InMemoryStorage` and `GoogleSheetsStorage`.
- `status_updated_at` gives the operator useful lifecycle timing without adding a full status-history table too early.

Trade-off:
A single `status_updated_at` field does not provide an audit trail. This is acceptable for M5 because the goal is operational visibility, not historical lifecycle analytics. A status-history entity or audit tab can be added later if validation feedback shows it is needed.

## M6 - Partial confirmation retry repair

Decision:
Keep the partial-confirmation repair path inside OrderService.confirm_order, but make it deterministic and narrow.

Detection criterion:
A product-level sale movement is considered already applied only when an existing StockMovement matches the expected sale movement exactly:

- tenant_id matches the order tenant_id.
- stock_movement_id equals mov_sale_{order_id}_{product_id}.
- product_id matches the aggregated order product_id.
- quantity_delta equals the negative aggregated ordered quantity for that product.
- reason is sale.
- reference_id equals the order_id.

This is not a heuristic. It does not use time windows, fuzzy matching, customer data, item names, or operator guesses.

Audit/logging behavior:
When an exact existing movement is used to repair a draft order, confirm_order emits a runtime warning log and then updates the order status to confirmed. No additional persistent audit row is created in M6 because stock_movements already contain the deterministic sale record and the order status update is still persisted. A dedicated audit/status-history table can be added later if operator feedback shows it is needed.

Permanence:
This is a permanent safety path, not a feature flag. It protects Sheets-backed confirmation retries from double-applying stock after a partial write.

Failure policy:
If an existing movement has a similar ID but does not match the exact expected sale payload, it is not treated as already applied. Normal stock validation and append-only duplicate protection apply, and the order remains draft if confirmation cannot complete.

Trade-off:
The repair path is intentionally conservative. It may refuse to repair some manually corrupted rows, but it avoids silently confirming an order against incorrect stock movement data.

## M6.5.2 - Request-scoped Sheets read consolidation

Decision:
Use an explicit request-scoped context manager for Google Sheets read reuse: `with sheets_request_context(storage):`.

The context is opened around the Streamlit page body after the storage instance is available and is closed by `__exit__` at the end of the script run.

Threading mechanism:
The context manager uses a module-level `ContextVar` to store the active request state. When active, `GoogleSheetsStorage` read methods reuse the same `_SheetsRecordSet` across storage method calls. When inactive, the storage behaves like M6.5.1: each public read method creates its own operation-scoped record set.

Convention:
Streamlit pages should wrap their read-heavy page body with `sheets_request_context(storage)`. Do not use `st.session_state` for request-scoped read reuse because it persists across reruns and would break the bounded-lifetime guarantee.

Nested contexts:
Nested Sheets request contexts are explicitly disallowed and raise `RuntimeError`. This keeps the request boundary unambiguous.

Why:
- StorageInterface remains unchanged.
- Services and UI semantics remain unchanged.
- The optimization stays behind storage/read-context internals.
- Deterministic tests can exercise the request boundary without importing Streamlit.
- Records are released on context exit, including exception paths.

Trade-off:
The page must explicitly define the request boundary after storage is available. This adds a small convention in Streamlit pages, but avoids hidden global caching and prevents stale reads from leaking across reruns.

## M6.5.3 - Short-TTL Sheets record cache

Decision:
Add a short-TTL, process-local, per-`GoogleSheetsStorage` instance cache for full-tab Google Sheets records.

Placement:
The cache sits behind the operation-scoped and request-scoped record layers. Request-scoped records are checked first through `_SheetsRecordSet`. When a tab is not already loaded in the active record set, `_load_records(...)` consults the cache before calling `get_all_records`.

Cache key:
Use `(spreadsheet_id, sheet_name)` as the cache key. Do not include `tenant_id` because `get_all_records` loads the full sheet tab and tenant filtering happens after records are hydrated or filtered by storage methods.

TTL:
Use a 30-second TTL. This is short enough to reduce repeated Streamlit rerun reads and live Sheets quota pressure while limiting stale-read risk during pilot usage.

Cache ownership:
The cache is attached to each `GoogleSheetsStorage` instance, not stored as a module-level singleton. This keeps runtime and test storage isolated and makes deterministic tests simpler.

Invalidation policy:
Invalidate affected tabs on every write through the storage layer:

- `upsert_product(...)` invalidates `products`.
- `create_customer(...)` invalidates `customers`.
- `create_order(...)` invalidates `orders` and `order_items`.
- `update_order_status(...)` invalidates `orders`.
- `append_stock_movement(...)` invalidates `stock_movements`.

Invalidation happens before the write. If a write partially succeeds or fails, the next read should hit Google Sheets instead of serving stale cached records.

Failure policy:
If `get_all_records` raises, the exception propagates and the cache key remains unset or is cleared. Failed reads must not poison the cache.

Safe-copy policy:
Cache hits return fresh record copies so caller-side mutation cannot corrupt cached state.

Trade-off:
This cache may briefly serve data up to 30 seconds old for reads that are not preceded by a write through this storage instance. The trade-off is acceptable for the current Google Sheets pilot because writes invalidate affected tabs and the main goal is to reduce repeated read pressure from Streamlit reruns.

## M6.5.4 - Locked dashboard read-budget verification

Decision:
Lock the dashboard prototype scenario as one future Streamlit page with eight widgets and a cold-cache read budget of no more than 4 full-sheet `get_all_records` calls per page render.

Locked scenario:
- Today's pulse: orders count today, revenue today, AOV today.
- Week trend: orders count and revenue per day for the last 7 days.
- Status breakdown: counts by draft, confirmed, completed, and cancelled.
- Time-of-day heatmap: weekday by hour order-count grid.
- Customer mix: new vs repeat customers this week.
- Top customers leaderboard: top customers by total spend.
- Top items this week: top products by quantity sold.
- Items frequently ordered together: top product pairs by co-occurrence count.

Required tabs:
- `orders`
- `order_items`
- `customers`
- `products`

Read budget:
The locked scenario requires exactly 4 tabs, so the target remains no more than 4 full-sheet reads per cold-cache page render.

Measurement methodology:
The scenario is defined once in `src/duna_orders/services/dashboard_read_scenario.py` and reused by both `scripts/measure_sheets_reads.py` and `tests/test_sheets_read_budget.py`. The measurement uses the storage layer with fake worksheets underneath, so `get_all_records` calls are counted without live Sheets.

Measured result:
- Total full-sheet reads: 4.
- `products`: 1.
- `customers`: 1.
- `orders`: 1.
- `order_items`: 1.
- `stock_movements`: 0.
- `parse_log`: 0.
- Result: pass.

Live Sheets delay comparison:
- Pre-M6.5 baseline: `LIVE_SHEETS_TEST_DELAY_S=12`.
- Post-M6.5.3 measurement: `LIVE_SHEETS_TEST_DELAY_S=3` still hit Google Sheets 429 quota errors.
- Post-M6.5.3 passing measurement: the next measured slower delay passed with 15 live_sheets tests.
- Because the suite still needs a delay, M6.5 improved deterministic read behavior and page-read budget readiness, but did not fully eliminate live-test quota sensitivity.

Rationale:
The dashboard scenario can meet the ≤4 budget because all eight widgets can be computed in Python from four full-tab reads. No widget requires per-cell, per-customer, per-product, or per-pair Sheets queries.

Trade-off:
The budget is defined for a cold-cache prototype page render. Warm-cache renders may be lower, but the cold-cache budget is the safer baseline for M7 dashboard implementation.

## M7.1 - Dashboard compute and Streamlit rendering split

Decision:
Keep dashboard computation in `src/duna_orders/services/dashboard.py` and Streamlit rendering in `src/duna_orders/ui/dashboard_streamlit.py`.

Timezone:
Dashboard date bucketing uses `America/Bogota`. Order timestamps are converted to local time before day/week aggregation.

Scope:
M7.1 implements only:
- today's pulse;
- week trend;
- status breakdown;
- customer mix.

The locked eight-widget dashboard scenario remains the source of truth, but later widgets are intentionally deferred to later M7 slices.

Why:
The pilot MVP currently uses Streamlit, but the product may later migrate to a web app or expose dashboard summaries through another channel. Keeping compute logic Streamlit-independent makes the aggregation layer reusable.

Rendering policy:
Streamlit helper functions receive already-computed result objects. They do not call storage and do not recompute business rules.

Request boundary:
The dashboard page wraps the full page body in one `sheets_request_context(storage)` and calls `run_locked_dashboard_read_scenario(...)` once.

Trade-off:
This adds one small UI module now, but avoids mixing dashboard business logic with Streamlit page code. No broader framework abstraction is introduced.

## M7.2 - Dashboard leaderboard rules

Decision:
Add dashboard leaderboard widgets for top customers and top items this week using the existing locked dashboard scenario records.

Top customers:
Rank qualifying customers by total spend descending. Break ties by customer name ascending. Exclude anonymous orders and orders whose `customer_id` is not found in the customer registry.

Top items:
Rank products by quantity sold descending. Break ties by product name ascending. Include items whose product is no longer present in the catalog, using `product_id` as the product-name fallback.

Currency formatting:
Dashboard display uses COP formatting as `COP 45.000`, with thousand-separator dots and no decimals for whole pesos. Formatting happens in Streamlit render helpers, not in compute functions.

Why:
The compute layer should preserve numeric values for later reuse in a web app, bot summary, or report. Display-specific formatting belongs in the UI layer.

Trade-off:
For deleted or missing catalog products, showing the `product_id` is less friendly than the old product name snapshot, but it makes the missing-catalog condition explicit and avoids silently presenting stale catalog names as current products.

## M7.3 - Dashboard analytical widgets

Decision:
Add the time-of-day heatmap and items-frequently-ordered-together widgets using the existing locked dashboard scenario records.

Time-of-day heatmap:
The heatmap uses a trailing 28-day window ending on `today`, inclusive. Orders are bucketed after converting timestamps to `America/Bogota`. Weekday encoding follows Python convention: `0=Monday` through `6=Sunday`.

The compute result always returns a full 7x24 grid with 168 cells, including zero-order cells. This keeps rendering simple and tests deterministic.

Product pairs:
Product pairs use the current week window from `week_start` through `week_start + 6 days`, matching the leaderboard widgets.

For each order, product IDs are deduplicated with a set before pair generation. This means duplicate item rows for the same product inside one order contribute only once to pair co-occurrence. Pairs are generated from `itertools.combinations(sorted(product_id_set), 2)` so `(A, B)` and `(B, A)` are canonicalized to the same pair.

Ranking:
Product pairs rank by count descending. Ties break by concatenated product IDs ascending for deterministic output.

Missing catalog products:
If a product ID is not present in the current catalog, the product ID is used as the display-name fallback, matching the M7.2 leaderboard convention.

Rendering:
The heatmap uses Streamlit’s Altair support with `st.altair_chart(...)` and `mark_rect`. Product pairs use `st.dataframe(...)`.

Empty state:
If there are no product pairs for the week, the UI shows `No pair data this week.`

Trade-off:
The heatmap is useful but visually denser than the previous widgets. M7.3 keeps rendering functional and defers layout polish to M7.4.

## M7.4 - Dashboard polish conventions

Decision:
Polish the dashboard without changing the locked scenario, widget set, storage reads, or service-layer contracts.

Section grouping:
Use three page sections:
- `Now`: today's pulse and status breakdown.
- `This week`: week trend, customer mix, top customers, top items, and item pairs.
- `Patterns`: time-of-day heatmap.

Rationale:
This grouping keeps the dashboard scannable by operator intent. `Now` gives immediate operating state, `This week` gives short-term business performance, and `Patterns` separates denser analytical behavior from the main operational summary.

Empty-state convention:
Use short, consistent empty-state captions:
- `No data for today.`
- `No data for this week.`
- `No data for this period.`

Formatting convention:
Dashboard render helpers own display formatting:
- COP values display as `COP 45.000`.
- Counts use thousand-separator dots when needed.
- Percentages display with one decimal place.
- Compute functions continue returning numeric values.

Error handling:
If the dashboard scenario load fails, the page shows a friendly message instead of a Streamlit traceback:
`Dashboard data could not be loaded. Refresh the page or check the Sheets connection.`

Trade-off:
The page now has slightly more rendering code, but the compute layer remains Streamlit-independent and reusable for a future web app or bot summaries.
## M7.6 - Dashboard demo realism and widget lock revision

Decision:
Revise the dashboard demo and locked widget set so the dashboard is suitable for realistic demos, not only technical verification.

Widget-set revision:

* `status_breakdown` was replaced by `week_over_week`.
* `top_items_this_week` was replaced by `top_items_by_category`.

Current locked dashboard widget set:

* `today_pulse`
* `week_over_week`
* `week_trend`
* `time_of_day_heatmap`
* `customer_mix`
* `top_customers`
* `top_items_by_category`
* `item_pairs`

Reference-date decision:
Use a dashboard reference-date resolver:

* Runtime mode uses the real current date.
* Demo mode uses the max local order date from the loaded orders.

Why:

* The demo dataset is fixed and deterministic.
* Without a demo reference date, current-period widgets become empty as real calendar time moves beyond the seeded order dates.
* Using the max local order date keeps the demo evergreen without requiring a reseed ritual.
* Runtime behavior remains unchanged because runtime mode still uses the real current date.

Demo realism decisions:

* Expanded the customer base from 30 to 730.
* Rebalanced orders into regular, medium-tail, and one-time customers.
* Replaced flat date cycling with deterministic demand-weighted daily volume.
* Added curated signature item weighting and Colombian restaurant pairings.
* Preserved deterministic generation through seed `42`.

Why:

* A 30-customer / 1,500-order dataset made the dashboard look artificial.
* The dashboard needs visible long-tail customers, realistic daily rhythm, stronger food-item signals, and meaningful item-pair patterns.
* The demo should be useful for internal validation and stakeholder conversations without touching runtime data.

Week-over-week decision:
Use week-to-date comparison:

* Current period: Monday through reference date.
* Previous period: previous Monday through the same weekday.

Metrics:

* Orders = total placed orders.
* Revenue = sum of non-cancelled orders.
* AOV = revenue divided by non-cancelled order count.
* Cancellation rate = cancelled orders divided by total placed orders.

Color semantics:

* Orders, Revenue, and AOV use higher-is-better logic.
* Cancellation rate uses lower-is-better logic.
* A lower cancellation rate shows a green down-arrow.
* A higher cancellation rate shows a red up-arrow.

Trade-off:
`week_over_week` removes the dedicated status breakdown widget, but cancellation visibility is preserved through cancellation rate while adding a stronger business comparison signal.

## M8.1A - Postgres foundation scope split

Decision:
Close M8.1A as a narrow Postgres foundation slice.

Completed foundation:

* SQLAlchemy 2.0 dependency and shared declarative metadata.
* Stable naming convention for future database constraints and indexes.
* Postgres engine/session utilities.
* Alembic scaffold wired to project settings and `Base.metadata`.
* Alembic scaffold tests that run without a real Postgres server.
* Generated `egg-info` cleanup and Ruff availability for future migration hooks.

Deferred to M8.1B:

* SQLAlchemy table models.
* First migration.
* `PostgresStorage`.
* Existing domain persistence parity.
* Runtime backend selection.

Why:
The original M8.1A description mixed foundation setup with storage implementation and table modeling. Splitting it keeps the first database change reviewable and low-risk. It also gives Claude a clean checkpoint before the project introduces schema design decisions that will be harder to reverse.

Trade-off:
M8.1A delivers less runtime functionality than the original slice wording implied, but it creates a safer base for M8.1B. The next slice must explicitly cover table models, migration generation, and `PostgresStorage` parity.

## M8.1B - PostgresStorage parity before runtime wiring

Decision:
Implement `PostgresStorage` behind the existing `StorageInterface` before changing runtime backend selection or Streamlit wiring.

Completed parity:

* Products.
* Customers.
* Orders.
* Order items.
* Stock movements.
* Parse logs.

Contract verification:
`PostgresStorage` was added to the shared storage contract fixture as a default non-live backend using a temporary SQLite-backed database. The storage contract now runs against memory and Postgres by default, while Sheets remains behind the `live_sheets` marker.

Why:
The service layer already depends on `StorageInterface`. Proving Postgres parity at that boundary keeps business logic independent from SQLAlchemy and avoids leaking database models into services or UI code.

Migration decision:
The first migration creates only the current non-WhatsApp runtime tables. WhatsApp-specific tables such as sessions, inbound messages, jobs, outbox, status events, and LLM call logs remain deferred to later M8 slices.

Datetime decision:
SQLite-backed tests return naive datetimes even when SQLAlchemy columns are declared with `timezone=True`. `PostgresStorage` normalizes datetimes read from the database to UTC-aware domain values so contract behavior remains stable in local tests.

Trade-off:
Using SQLite for default PostgresStorage tests does not prove every Postgres-specific behavior. It is still useful for fast contract parity and row-to-domain mapping. Live Postgres or Neon verification remains a separate future slice.
## M8.1C-0 - Live Postgres verification before runtime wiring

Decision:
Add a live Postgres verification harness before changing runtime backend selection, Streamlit wiring, demo seeding, or dashboard behavior.

Scope:
The live harness uses the existing `DATABASE_URL`, Alembic configuration, and `PostgresStorage`.

It verifies:

* Alembic `upgrade head` against real Neon Postgres.
* Basic `PostgresStorage` product persistence.
* Basic `PostgresStorage` customer persistence.
* Basic `PostgresStorage` order and order-item persistence.

Test isolation:
The live storage smoke test creates UUID-based IDs and a unique temporary `tenant_id`. Cleanup deletes only rows matching that temporary tenant.

Why:
M8.1B proved storage parity through fast SQLite-backed tests, but SQLite does not prove all real Postgres behavior. Before using Postgres as the runtime backend or reseeding demo data into Postgres, the migration and storage layer must be exercised against a real Postgres database.

Trade-off:
The live test depends on external infrastructure and is slower than the default suite. It stays behind the `live_postgres` marker and remains excluded from default test runs.

## M8.1C-1B - Postgres demo reseed with bulk helpers

Decision:
Add Postgres-specific bulk seeding helpers as trusted seeding/migration utilities, not as `StorageInterface` methods.

Details:

* Bulk helpers use SQLAlchemy insert-many through `session.execute(insert(Model), rows)`.
* Bulk helpers reuse the same domain-to-row value mapping used by single-row Postgres creates.
* `PostgresStorage.reseed_demo_dataset(...)` performs wipe-then-seed inside one transaction.
* If any insert fails, the transaction rolls back and avoids a partial reseed.
* Tenant-scoped wipe requires a non-empty `tenant_id`.
* Every delete path applies `WHERE tenant_id = ...`.
* The wipe clears all six tenant tables: order items, stock movements, parse log, orders, products, and customers.
* The seed inserts only the four entity types present in `DemoDataset`: products, customers, orders, and order items.
* The live reseed test intentionally leaves the deterministic demo dataset in the dev Postgres branch. The test is idempotent because reseed starts with a tenant-scoped wipe.

Why:
M8.1C-1A made demo data generation storage-agnostic. M8.1C-1B adds a fast, repeatable way to materialize that locked dataset into Postgres before runtime backend selection or dashboard parity work.

Additional batching decision:
The live Neon diagnostic showed that order bulk inserts fragmented into 964 cursor executions for 1500 rows because heterogeneous nullable order fields produced many insert shapes. We fixed this by applying `render_nulls=True` to the `OrderRow` bulk insert, forcing a uniform column set with explicit NULLs.

Correctness check:
The nullable order fields that receive explicit NULLs have no server defaults, so rendering NULL does not override any database-generated value. This preserves the single-row persistence contract while restoring batching.