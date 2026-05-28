# Architectural Decisions
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
