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