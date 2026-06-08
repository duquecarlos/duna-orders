# M8.2 Operator Review Design

Base commit: `a35667f`

M8.2 designs the operator-facing review surface for inbound-created drafts proven by M8.1.4. It is a human-in-the-loop gate only: operators review parsed inbound drafts and choose approve or reject. Implementation is split into a service contract slice and a later Streamlit UI slice.

## 1. Final Pre-flight Findings

Current `OrderStatus` values are:

```text
draft
confirmed
in_preparation
ready
delivered
picked_up
cancelled
```

There is no current `approved` or `rejected` status.

`OrderService.confirm_order(...)` currently moves `draft -> confirmed`, but it is not safe for M8.2 approval because it mutates inventory before status update:

- appends sale stock movements;
- updates product stock;
- then updates order status to `confirmed`.

`OrderService.transition_order_status(...)` is status-only, but current transition validation does not allow `draft -> approved` or `draft -> cancelled`.

Lifecycle-safe status mutation exists through `PostgresOrderLifecycleStore.update_order_status_with_transition(...)`, where the order status update and lifecycle transition row are persisted together.

`services/dashboard.py` is Streamlit-free and should remain pure compute.

`DECISIONS.md` already records the deferred gap that `confirm_order(...)` stock/product updates are not in the same Postgres transaction as the status plus lifecycle transition.

Traceability findings:

- `processed_messages.resulting_order_id -> orders.order_id` is the durable traceability source of truth.
- `processed_messages.raw_body` is the primary raw inbound message display source.
- `orders.raw_message` is redundancy/fallback only.
- `parse_log` must not be used for order pairing because it has no durable `order_id` or `message_sid`.
- Current persisted fields support deterministic traceability, but the current API surface needs a small processed-message lookup by `resulting_order_id/order_id`.

## 2. Final Q1-Q5 Decisions

### Q1. Empty parse / no-order

Keep out of M8.2.

Messages that create `processed_messages` and `parse_log` rows but no order are not reviewable drafts. They belong in a later inbound inbox / parse-failure review slice.

### Q2. Reject semantics

Reject moves `draft -> cancelled`.

The UI label is "Reject draft".

Known compromise:

- rejected inbound drafts count as `cancelled`;
- dashboard cancellation metrics may include operator-rejected drafts;
- distinguishing operator-rejected drafts from genuine customer/business cancellations is deferred;
- no reject reason is added in M8.2.

### Q3. Approval semantics

Add a proposed new `approved` status.

Status meanings:

- `draft` = parsed order, not yet reviewed by operator;
- `approved` = operator reviewed and accepted the inbound draft, but inventory/customer confirmation has not been committed;
- `confirmed` = existing full business confirmation path, including current inventory side effects;
- `cancelled` = rejected/cancelled order.

M8.2 approval must not use `OrderService.confirm_order(...)`.

M8.2 approval must be:

- status-only;
- lifecycle-logged;
- routed through `OrderService` plus lifecycle store;
- no stock movement;
- no product update;
- no outbound customer message.

M8.2.1A only adds:

- `draft -> approved`;
- `draft -> cancelled`.

The `approved -> confirmed` inventory/business confirmation path is explicitly deferred to a later design slice.

### Q4. Tenant scoping

Tenant flows explicitly into service functions as `tenant_id`.

For the current Streamlit demo, the active tenant is derived from the existing demo catalog/settings path. M8.2 does not add broad tenant-selector work.

### Q5. Concurrency / staleness

Minimum behavior for a single-operator demo:

- re-fetch the order immediately before approve/reject;
- refuse the action unless the current status is still `draft`.

Stronger locking or versioning is deferred.

## 3. Locked-principle Checklist

- Storage remains pure persistence. List/filter logic lives in the service layer, not storage.
- No `StorageInterface` or storage-signature changes.
- M8.2 is review plus approve/reject only.
- No outbound, message-copy generation, conversation state, auto-confirmation, queue/worker, or parser changes.
- Approve/reject are explicit operator actions. Nothing moves off `draft` automatically.
- Lifecycle transitions go through `OrderService` plus lifecycle store, reusing the M8.1.4 protected path.
- `services/dashboard.py` stays Streamlit-free. The Streamlit view is glue only.

## 4. Proposed Service Signatures

```python
@dataclass(frozen=True)
class InboundDraftReviewItem:
    order: Order
    message_sid: str
    raw_inbound_body: str
    from_number: str | None
```

```python
class InboundDraftReviewService:
    def list_reviewable_inbound_drafts(
        self,
        *,
        tenant_id: str,
    ) -> list[InboundDraftReviewItem]:
        ...
```

```python
class OrderService:
    def review_inbound_draft(
        self,
        *,
        order_id: str,
        tenant_id: str,
        decision: Literal["approve", "reject"],
        reviewed_at: datetime | None = None,
    ) -> Order:
        ...
```

Decision mapping:

- `approve -> approved`;
- `reject -> cancelled`.

Required behavior:

- re-fetch order by `order_id`;
- require matching `tenant_id`;
- require current status `draft`;
- use the lifecycle store path for status plus transition row;
- transition source is `operator`;
- no inventory mutation;
- no customer message;
- no outbound.

Processed-message read API:

```python
class PostgresProcessedMessageStore:
    def get_message_for_order(
        self,
        *,
        order_id: str,
        tenant_id: str,
    ) -> ProcessedMessage | None:
        ...
```

This lookup uses `processed_messages.resulting_order_id`, not timestamp proximity.

## 5. Implementation Split

### M8.2.1A - Domain/service contract

Scope:

- add `approved` to `OrderStatus`;
- update status transition validation for `draft -> approved` and `draft -> cancelled`;
- add status-only lifecycle-safe review transition in `OrderService`;
- add processed-message lookup by `resulting_order_id/order_id`;
- add focused tests for lifecycle logging, no stock/product mutation, stale status refusal, tenant scoping, and raw-message trace lookup;
- no Streamlit yet.

Out of scope for M8.2.1A:

- `approved -> confirmed`;
- inventory/business confirmation after approval.

### M8.2.1B - Streamlit operator review surface

Scope:

- list inbound-created draft orders only;
- show `processed_messages.raw_body` beside parsed order fields/items;
- design the view for catchability: raw message and parsed draft must be visually comparable so an operator can spot wrong quantities, missed modifiers, wrong fulfillment, or wrong payment method;
- add approve/reject buttons;
- re-fetch before action;
- no outbound and no customer message generation.

## 6. Will Touch / Will Not Touch

Will touch in implementation:

- `OrderStatus` enum;
- status transition validation;
- `OrderService` status-only review transition;
- lifecycle store path usage;
- processed-message read API by `resulting_order_id/order_id`;
- focused tests;
- Streamlit operator review page/glue in M8.2.1B;
- dashboard/status-filter compatibility checks as needed, without dashboard redesign.

Will not touch:

- `StorageInterface`;
- parser behavior;
- `parse_log` pairing logic;
- outbound messaging;
- conversation state;
- queue/worker;
- auto-confirmation;
- product stock mutation for M8.2 approval;
- stock movement creation for M8.2 approval;
- reject reasons;
- dashboard redesign.

## 7. Explicit Deferred Items

- Empty-parse/no-order inbound inbox.
- Parse-failure review.
- `approved -> confirmed` inventory/business confirmation path.
- Distinguishing operator-rejected drafts from genuine customer/business cancellations.
- Stronger locking/versioning for stale approve/reject actions.
- Outbound messaging.
- Conversation state.
- Auto-confirmation.
- Queue/worker.
- Reject reasons.
- Dashboard redesign.

## 8. M8.2.1C Manual UI Smoke Closeout

Manual operator review UI smoke passed with no code changes at baseline:

```text
6d7673c feat(ui): add inbound draft review page
fa9ba14 feat(orders): add inbound draft review listing service
7c5600b feat(orders): add inbound draft review contract
```

Smoke used Postgres with tenant `el-fogon-colombiano` and active demo business
`El Fogón Colombiano`. The local `.env` default remained
`DUNA_STORAGE_BACKEND=memory`, with `DATABASE_URL` configured,
`WEBHOOK_TENANT_ID=el-fogon-colombiano`, and `DASHBOARD_TARGET=demo`.

Verified:

- memory and Sheets backends showed the Postgres-only unavailable state;
- headless Streamlit served the inbound review page with HTTP 200;
- linked draft `ord_01ktjxxdpesn3tc5by46hhz5v1` from message
  `SM4e676d966f0a822e15fc068dcfc71e8c` rendered raw inbound text beside parsed
  items, modifiers, fulfillment/payment details, and total `$85.000`;
- approve moved the order `draft -> approved`, appended lifecycle source
  `operator`, and removed the order from the review list;
- `confirmed_at` stayed unset, product stock was unchanged, no order stock
  movements were created, and no outbound behavior occurred.

Reject smoke was not run because no second linked draft remained. No smoke data
was created without approval.
