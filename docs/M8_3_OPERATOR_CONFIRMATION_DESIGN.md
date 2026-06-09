# M8.3 Operator Confirmation Design

Base commit: `08380f5`

M8.3 designs the explicit operator action that commits an already reviewed
`approved` order to inventory. It is the first side-effectful lifecycle
transition in the inbound operator flow, so the design is Postgres-first and
transaction-bounded.

## 1. Pre-flight Findings

Repo-grounded findings accepted for this design:

- `OrderService.confirm_order(...)` is not atomic across all inventory, order,
  and lifecycle side effects.
- Current Postgres storage methods commit per method call.
- `confirm_order(...)` can append stock movements and update products before a
  later status/lifecycle failure, which is a live stock-corruption path.
- `PostgresOrderLifecycleStore.update_order_status_with_transition(...)` only
  makes the final status update plus lifecycle transition atomic together.
- True atomic `approved -> confirmed` is feasible on Postgres through a bounded
  Postgres transactional contract.
- Memory has no durable rollback guarantee.
- Google Sheets cannot atomically roll back across stock movements, product
  rows, order status, `confirmed_at`, and lifecycle rows.
- Inbound operator review is already effectively Postgres-specific because
  processed-message review is Postgres-specific.

## 2. Lifecycle Model

Implemented through M8.2:

- `draft` = parser-created, not operator-reviewed.
- `approved` = operator verified the parse. No inventory implication.
- `confirmed` = business/inventory committed: stock validated, sale movements
  created, product stock decremented, and `confirmed_at` set.
- `cancelled` = rejected/cancelled.

`OrderService.review_inbound_draft(...)` already handles `draft -> approved`
and `draft -> cancelled` as status-only, lifecycle-logged transitions with
`source="operator"`.

M8.3 adds the missing `approved -> confirmed` transition. It is explicit,
operator-triggered, inventory-committing, and has no outbound customer message.

## 3. Decision 1 - Service/API Shape

Decision: refactor toward a shared atomic confirmation core with two
precondition-guarded public service methods.

Public methods:

```python
class OrderService:
    def confirm_approved_order(
        self,
        *,
        order_id: str,
        tenant_id: str,
        confirmed_at: datetime | None = None,
    ) -> Order:
        ...

    def confirm_order(
        self,
        order_id: str,
        confirmed_at: datetime | None = None,
    ) -> Order:
        ...
```

`confirm_approved_order(...)` is the new M8.3 path. It enforces
`status == "approved"` and then calls the shared confirmation core.

Legacy `confirm_order(...)` remains the existing manual order confirmation API.
It will be re-pointed onto the same core in a separate slice so the transaction
fix eventually covers both paths without silently changing legacy behavior in
the new-path slice.

Rationale:

- Reusing only `confirm_order(...)` would blur `draft -> confirmed` legacy
  behavior with the new `approved -> confirmed` operator action.
- Adding `confirm_approved_order(...)` makes the M8.3 precondition explicit.
- A shared core avoids duplicating stock validation, movement creation, product
  decrements, status update, `confirmed_at`, and lifecycle logic.
- The core must not inherit the legacy transaction gap by composing existing
  per-method Postgres storage calls.

## 4. Decision 2 - Atomic-Commit Capability Boundary

Decision: express atomic confirmation as a Postgres storage-layer transaction
capability. Memory and Google Sheets do not implement the real capability.

This is a clean capability gap at the storage boundary, not an ad-hoc
`if backend == postgres` branch inside service logic.

Service orchestration responsibilities:

- require explicit `tenant_id` for the new approved-confirmation path;
- load and validate the order belongs to the tenant;
- enforce the caller-specific precondition;
- construct the requested lifecycle transition metadata;
- call the storage-layer atomic confirmation capability;
- surface unsupported-backend and duplicate-movement failures cleanly.

Postgres storage transaction responsibilities, all inside one commit:

1. Re-read the order and relevant rows inside the transaction.
2. Validate current status still matches the expected `from_status`.
3. Validate product rows exist.
4. Validate available stock.
5. Fail hard if any expected sale stock movement already exists.
6. Insert sale stock movement rows.
7. Decrement product stock.
8. Set `confirmed_at`.
9. Set order status to `confirmed`.
10. Append lifecycle transition row with the caller-provided `from_status`,
    `to_status="confirmed"`, and source.
11. Commit once.

Any failure rolls back all of the above.

Failure-injection rollback tests must prove that if a failure occurs after any
intermediate write, all effects roll back:

- no sale stock movement rows remain;
- product stock is unchanged;
- order status is unchanged;
- `confirmed_at` is unchanged;
- no lifecycle transition row is appended.

## 5. Decision 3 - Backend Support Matrix

| Backend | M8.3 support | Behavior |
| --- | --- | --- |
| Postgres | Full support | Atomic `approved -> confirmed` through one transaction |
| Memory | Unit-level only if needed | No real durability or rollback guarantee |
| Google Sheets | Unsupported for `confirm_approved_order(...)` | Raise a clear not-supported error |

Postgres is the target backend for M8.3.

Memory may be useful for unit tests or local demonstrations, but the design must
not claim real atomicity there.

Google Sheets must not offer best-effort `confirm_approved_order(...)`. A
partial Sheets commit can silently corrupt inventory with no rollback path. The
approved-confirmation path should raise a clear unsupported-backend error when
the active backend lacks the atomic confirmation capability.

## 6. Decision 4 - Formal Transition Rule

Decision: `approved -> confirmed` becomes an explicit lifecycle transition rule
in M8.3. It is not private service behavior.

Required transition:

- `from_status="approved"`;
- `to_status="confirmed"`;
- `source="operator"`;
- `confirmed_at` set to the supplied timestamp or current UTC time.

This preserves the M8.2 meaning of `approved`: parse review accepted, but no
inventory implication yet.

## 7. Decision 5 - Precondition-Agnostic Core

Decision: the shared internal confirmation core must be precondition-agnostic.
It must not hardcode `approved -> confirmed` or `source="operator"`.

Each public caller supplies its own expected source status and lifecycle source.

New M8.3 path:

- public method: `confirm_approved_order(...)`;
- real precondition: current order status must be `approved`;
- transition: `approved -> confirmed`;
- source: `operator`;
- tenant_id: explicit.

Legacy path:

- public method: `confirm_order(...)`;
- real current precondition from repo truth: current order status must be
  `draft`;
- transition: `draft -> confirmed`;
- source: `operator`;
- tenant_id: currently inferred from the order, because the legacy signature
  does not accept tenant_id.

The core receives the expected `from_status`, target `to_status`, transition
source, order id, tenant scope where available, and `confirmed_at`. It performs
the same inventory commit mechanics for both paths without conflating their
preconditions.

## 8. Decision 6 - Duplicate Stock Movements

Decision: fail hard in the atomic core if any expected sale stock movement
already exists.

M8.3 does not implement repair or idempotency for duplicate stock movements.
That behavior needs a separate design if real traffic requires it.

Data-layer behavior:

- detect each expected sale movement id before inserting;
- if any expected movement exists, abort the transaction;
- do not decrement product stock;
- do not change order status;
- do not set `confirmed_at`;
- do not append lifecycle transition.

UX behavior:

- the operator must see a comprehensible message, not a crash;
- recommended message shape: "This order appears already confirmed or has
  existing stock movements. Do not retry; escalate for manual review."

Deferral to record in `DECISIONS.md`: duplicate sale movement idempotency/repair
is out of M8.3 and will require a separate design.

## 9. Decision 7 - Outbound Separation

Decision: `approved -> confirmed` does not send any customer message.

Rationale:

- Twilio/customer messaging is not part of the inventory transaction.
- Coupling outbound sends to inventory commits creates retry and resend
  entanglement.
- A message-send failure must not roll back committed inventory.
- A transaction failure must not accidentally send a customer confirmation.

Outbound customer messaging remains a separate future slice.

## 10. Exact M8.3 Build Contracts

### Public Service Method - New Path

```python
def confirm_approved_order(
    self,
    *,
    order_id: str,
    tenant_id: str,
    confirmed_at: datetime | None = None,
) -> Order:
    ...
```

Required behavior:

- validate order exists for `tenant_id`;
- require current status `approved`;
- call the atomic confirmation core;
- return the confirmed `Order`;
- raise a clear unsupported-backend error when atomic confirmation capability is
  not available;
- no outbound.

### Public Service Method - Legacy Path

```python
def confirm_order(
    self,
    order_id: str,
    confirmed_at: datetime | None = None,
) -> Order:
    ...
```

Required behavior after M8.3.1B:

- preserve legacy `draft -> confirmed` behavior;
- preserve `source="operator"`;
- route Postgres through the same atomic core;
- keep non-Postgres behavior explicitly best-effort or unchanged as decided in
  M8.3.1B;
- no silent behavior change beyond closing the Postgres transaction gap.

### Shared Confirmation Core

The shared core owns:

- stock aggregation by product;
- product existence checks;
- stock validation;
- duplicate movement detection;
- sale stock movement creation;
- product stock decrement;
- `confirmed_at` update;
- status update to `confirmed`;
- lifecycle transition row creation;
- transaction boundary for Postgres;
- rollback on failure.

The core does not own:

- parser behavior;
- outbound messaging;
- payment verification;
- UI concerns;
- payment status updates;
- stock reversal on later cancellation.

## 11. Legacy Refactor Risk

Legacy `confirm_order(...)` must be refactored in its own slice, M8.3.1B.

Before re-pointing it to the shared core:

1. Map every current caller of `confirm_order(...)`.
2. Add characterization tests capturing current legacy behavior.
3. Refactor onto the core.
4. Prove behavior is preserved except that the Postgres transaction gap is
   closed.

Risks:

- legacy precondition differs from the new path (`draft` vs `approved`);
- legacy source and tenant handling come from current repo behavior and must not
  shift accidentally;
- legacy may have thinner test coverage, so silent behavior changes could go
  uncaught;
- existing callers will inherit now-atomic behavior and may now fail hard on
  partial states they previously tolerated.

Do not bundle this refactor with the new `confirm_approved_order(...)` slice.

## 12. M8.3 Implementation Split

### M8.3.1A - Postgres Atomic Core + `confirm_approved_order(...)`

Scope:

- add the Postgres atomic confirmation capability;
- add `OrderService.confirm_approved_order(...)`;
- enforce `status == "approved"`;
- add formal `approved -> confirmed` transition rule;
- fail hard on duplicate sale stock movements;
- add rollback tests with injected mid-commit failure;
- assert full rollback of product stock, stock movements, status,
  `confirmed_at`, and lifecycle transition;
- raise clean unsupported-backend errors for Memory and Sheets.

Legacy `confirm_order(...)` remains untouched in M8.3.1A.

No UI.

### M8.3.1B - Legacy `confirm_order(...)` Refactor

Scope:

- map current callers;
- add characterization tests first;
- re-point legacy Postgres `confirm_order(...)` onto the shared core;
- preserve `draft -> confirmed` semantics and `source="operator"`;
- prove the transaction gap is closed.

No UI.

If this proves risky, the demo can ship after M8.3.1A with the legacy manual
path's transaction gap documented as still open only for the manual path.

### M8.3.1C - Streamlit Confirm Action + Manual Smoke

Scope:

- add an "Approved orders" section to the existing inbound review page;
- do not add a new page;
- visually separate approve/reject from irreversible inventory confirmation;
- require an explicit confirmation step because no stock-reversal design exists;
- call `confirm_approved_order(...)`;
- show clean unsupported-backend and duplicate-movement errors;
- run manual smoke on a throwaway Neon branch following M8.1.4 discipline.

Manual smoke must verify:

- stock decrements;
- `confirmed_at` is set;
- order moves `approved -> confirmed`;
- lifecycle transition source is `operator`;
- no outbound occurs;
- rollback on forced failure if testable.

## 13. Fail-Hard Behavior and Operator UX

Failure classes that should be cleanly surfaced:

- unsupported backend;
- stale/non-approved order;
- tenant mismatch;
- insufficient stock;
- missing product;
- duplicate expected sale stock movement;
- transaction failure.

The UI should render these as operator-facing messages. It must not expose raw
tracebacks as the normal path.

For duplicate sale movement state, preferred operator text:

```text
This order appears already confirmed or has existing stock movements.
Do not retry; escalate for manual review.
```

## 14. Dashboard and Status Semantics

- `approved` is an active pending state: reviewed by a human, not inventory
  committed.
- `confirmed` is inventory/business committed.
- `confirmed_at` becomes the future funnel timestamp for business confirmation.
- `cancelled` still includes both rejected inbound drafts and other
  cancellations; rejected-vs-cancelled ambiguity remains deferred.
- Cancellation after `confirmed` requires a future stock-reversal design.
  M8.3 must not silently treat confirmed cancellation as stock-neutral.

## 15. Payment Model - Forward Design, Not Built in M8.3

Colombian payment reality:

- Transfer methods such as Nequi or bank transfer usually require payment before
  preparation.
- The customer sends a comprobante screenshot.
- The operator verifies the comprobante.
- Then the kitchen starts.
- Cash can be collected at delivery or pickup, so kitchen work may start before
  collection.

Payment should be modeled as an orthogonal dimension, not encoded into the
linear fulfillment status enum.

Forward model:

- add `payment_status`, for example `pending`, `verified`, `collected`;
- later enforce `confirmed -> in_preparation` preconditions:
  `(payment_method == "efectivo") OR (payment_status == "verified")`;
- keep payment independent from `approved -> confirmed`.

M8.3 transaction remains inventory-only. It does not verify payment, capture
payment evidence, or gate confirmation on payment.

Hidden dependency:

- transfer verification requires the operator to view a payment screenshot sent
  over WhatsApp;
- the current inbound pipe handles text only, not media;
- transfer-payment verification depends on a future inbound media capture slice:
  receive, store, link, and display comprobante.

Open question:

- Add `payment_status` to the order model now, or later?

Recommendation: add it later. Adding it now would require schema, UI, parser,
and operational decisions that are not needed to make `approved -> confirmed`
atomic. M8.3 should keep payment as documented forward design only.

## 16. Explicitly Out of Scope

- No outbound/customer messaging.
- No fulfillment status work beyond documenting future implications.
- No payment-status enforcement.
- No payment gate.
- No inbound media/comprobante capture.
- No stock reversal on cancellation after confirmation.
- No duplicate movement idempotency or repair.
- No Memory/Sheets real support for `confirm_approved_order(...)`.
- No auto-confirmation.
- No new UI page.
- No editing approved orders before confirmation. If wrong, cancel; do not edit.
- No system-wide transaction audit.
- No conversation state.
- No queue/worker.
- No parser changes.
- No dashboard redesign.

## 17. What M8.3 Will Touch

Expected implementation touch points:

- `OrderService` confirmation surface;
- Postgres-specific atomic confirmation capability;
- formal lifecycle transition validation for `approved -> confirmed`;
- focused service/storage tests;
- rollback/failure-injection tests;
- Streamlit inbound review page in M8.3.1C;
- docs and `DECISIONS.md` deferral entries.

## 18. What M8.3 Will Not Touch

- Broad `StorageInterface` transaction abstraction.
- Parser behavior.
- Outbound WhatsApp.
- Customer notification generation.
- Payment verification.
- Inbound media.
- Queue/worker.
- Conversation state.
- Dashboard redesign.
- Stock reversal after confirmed cancellation.

## 19. DECISIONS.md Deferrals to Record

Record these when implementation begins:

- Duplicate sale stock movement idempotency/repair is deferred. M8.3 fails hard.
- Cancellation after `confirmed` needs a future stock-reversal design.
- Transfer-payment verification requires future inbound media capture.
- `payment_status` is deferred until the payment workflow slice.
