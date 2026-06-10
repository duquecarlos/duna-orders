# M9.2A Conversation Advancement Service Design

Status: design refinement only.

Baseline: `64fc050 docs(m9): close conversation store foundation`

M9.2 resolves the seam between accumulated conversation turns and the existing
operator-reviewable draft order. The downstream lifecycle remains unchanged:

```text
draft -> approved -> confirmed -> atomic inventory commit -> outbound acknowledgement
```

M9.2A is docs/design only. M9.2B and M9.2C implement later.

## 1. Pre-flight findings

`OrderService.create_draft(request: DraftOrderRequest) -> Order` is the current
draft boundary.

It:

* accepts one `DraftOrderRequest`;
* filters request items to positive quantities;
* raises `EmptyDraftError` when no positive items remain;
* validates each product through storage;
* raises `ProductNotFoundError` or `InactiveProductError`;
* computes item totals, subtotal, packaging fee, delivery fee, and total;
* normalizes customer phone through existing domain phone logic;
* creates or links a customer by phone;
* creates an `Order(status="draft")`;
* optionally records the initial lifecycle transition;
* returns the created draft `Order`.

Current `DraftOrderRequest` and `Order` do not carry `conversation_id`.
Adding nullable `conversation_id` is additive and does not require lifecycle,
confirmation, parser, webhook, UI, outbound, or `StorageInterface` changes.

M9.1 `ConversationStateStore` currently exposes exactly four methods:

* `get_or_create_open_session(...)`;
* `append_turn_if_new(...)`;
* `list_turns(...)`;
* `get_session(...)`.

M9.1 `conversation_sessions` fields:

* `conversation_id`;
* `tenant_id`;
* `customer_phone`;
* `status`;
* `opened_at`;
* `last_message_at`;
* `version`;
* `created_at`;
* `updated_at`.

M9.1 `conversation_turns` fields:

* `turn_id`;
* `conversation_id`;
* `tenant_id`;
* `message_sid`;
* `from_number`;
* `body`;
* `received_at`;
* `sequence_number`;
* `created_at`.

Conversation status type includes `open`, `draft_created`, `expired`, and
`failed`, but M9.1 only writes `open`.

`ParsingService.parse(...)` accepts `tenant_id`, `raw_message: str`, and
`products`. It logs `PROMPT_VERSION` and delegates to the existing stateless
parser interface:

```text
parse(raw_message, products) -> ParseResult
```

Passing a rendered transcript as `raw_message` does not require a parser
interface change, but the current prompt is not transcript-specific. M9.2 must
not change parser prompt text or `PROMPT_VERSION`.

Product context must use `TenantScopedReadService.list_products(...)`. M9.2C
must add the advancement service module to the AST broad-read guard.

The outbound acknowledgement store is the precedent for using a narrow
Postgres-backed persistence boundary outside `StorageInterface`. It uses
database uniqueness and claim-before-act behavior to make side effects
idempotent. M9.2 applies the same discipline to draft creation by making the
draft identifiable by conversation id.

## 2. Central decision: orphan-draft idempotency

Decision: choose option A plus option B.

M9.2B will:

* add nullable `conversation_id` to `DraftOrderRequest`;
* add nullable `conversation_id` to `Order`;
* add nullable `conversation_id` to persisted orders;
* add a unique non-null `conversation_id` constraint/index on orders;
* add nullable `resulting_order_id` to `conversation_sessions`;
* add `mark_draft_created(tenant_id, conversation_id, order_id)`;
* add a narrow Postgres-backed conversation/order lookup helper outside
  `StorageInterface` for finding existing orders by `conversation_id`.

M9.2C will:

* pass `conversation_id` into the draft request before calling
  `OrderService.create_draft(...)`;
* recover orphan drafts by looking up an existing order by `conversation_id`;
* return `ALREADY_HAS_DRAFT` when the draft already exists.

Rationale:

* M9.2 must perform two writes with no shared transaction: create draft, then
  mark the conversation `draft_created`.
* The only safe write order is draft first, then mark conversation.
* If the process crashes after draft creation but before marking conversation,
  the conversation remains `open`, but a draft exists.
* A retry must not create a second draft.
* Carrying `conversation_id` on the order gives the retry a durable recovery
  key.
* A unique non-null `conversation_id` constraint prevents concurrent duplicate
  drafts by construction.
* Operator review is not the duplicate-prevention mechanism.

The unique constraint is one order row per non-null `conversation_id`. It is
not status-dependent. The linked draft may later become approved or confirmed,
but it is still the same order produced by the conversation. The constraint is
for conversation-origin idempotency, not lifecycle policy.

## 3. Lookup boundary

Existing order lookup by `conversation_id` must be provided by a narrow
Postgres-backed conversation/order lookup helper outside `StorageInterface`.

Do not extend `StorageInterface`.

Do not make `OrderService` own conversation lookup.

The lookup is persistence support for the advancement service. It is not order
lifecycle behavior.

Recommended shape:

```text
ConversationOrderLookup.get_order_by_conversation_id(
    *,
    tenant_id: str,
    conversation_id: str,
) -> Order | None
```

This helper may live beside the conversation persistence boundary because it
exists only to support conversation-origin idempotency.

## 4. Implementation split

### M9.2A - Design refinement

Scope:

* create this design document;
* update architecture/roadmap/decision/changelog docs.

No code, tests, migrations, commit, or push.

### M9.2B - Schema/domain/persistence only

Status: implemented in `9677ded feat(m9): add conversation draft links`.

Scope:

* nullable `conversation_id` on `DraftOrderRequest`;
* nullable `conversation_id` on `Order`;
* nullable `conversation_id` on persisted orders;
* unique non-null `conversation_id` constraint/index on orders;
* nullable `resulting_order_id` on `conversation_sessions`;
* `mark_draft_created(tenant_id, conversation_id, order_id)`;
* narrow Postgres-backed order lookup by `conversation_id` outside
  `StorageInterface`.

Explicitly excluded:

* parser calls;
* service orchestration;
* webhook wiring;
* UI;
* draft advancement flow.

### M9.2C - Advancement service

Scope:

* append inbound turn;
* render deterministic transcript from canonical turns;
* call existing `ParsingService`;
* apply deterministic operator-reviewable draft completeness rule;
* run orphan-draft idempotency guard;
* create draft through existing `OrderService.create_draft(...)`;
* mark conversation `draft_created`;
* return observable advancement outcome.

### M9.3 - Webhook wiring

Scope:

* replace current direct one-message draft creation with conversation
  advancement;
* preserve Twilio signature validation;
* preserve `processed_messages` first-gate idempotency;
* preserve tenant-scoped product reads.

## 5. Service contract

Input:

* `tenant_id`;
* `message_sid`;
* `from_number`;
* `body`;
* `received_at`.

Output:

* `conversation_id`;
* `turn_appended`;
* `draft_created`;
* `resulting_order_id`;
* `outcome`.

Outcome enum:

* `TURN_APPENDED_INCOMPLETE`;
* `PARSE_INCOMPLETE`;
* `DRAFT_CREATED`;
* `ALREADY_HAS_DRAFT`;
* `DUPLICATE_MESSAGE`.

Outcome semantics:

* `DUPLICATE_MESSAGE`: no second turn, no parser call, no draft creation.
* `TURN_APPENDED_INCOMPLETE`: turn appended, but no parser-ready or
  draft-ready shape exists.
* `PARSE_INCOMPLETE`: parser ran, but did not produce an operator-reviewable
  draft shape.
* `DRAFT_CREATED`: draft was created and conversation marked
  `draft_created`.
* `ALREADY_HAS_DRAFT`: service found an existing order for the conversation or
  recovered the orphan-draft crash window.

## 6. Idempotency and crash-window strategy

The service must be safe if called twice with the same `message_sid`, even
without webhook-level `processed_messages`.

Duplicate `message_sid` behavior:

* no second turn;
* no parser call;
* no draft creation;
* return `DUPLICATE_MESSAGE`.

Draft creation flow:

1. Get or create open conversation.
2. Append turn idempotently.
3. If duplicate turn, return `DUPLICATE_MESSAGE`.
4. If conversation already has `resulting_order_id`, return
   `ALREADY_HAS_DRAFT`.
5. Look up existing order by `conversation_id`.
6. If found, call `mark_draft_created(...)` and return `ALREADY_HAS_DRAFT`.
7. Render transcript and parse.
8. Validate completeness.
9. Create draft with `conversation_id`.
10. Mark conversation `draft_created`.
11. Return `DRAFT_CREATED`.

No cross-store transaction is used.

Required write order:

```text
create draft -> mark conversation draft_created
```

Reverse order is rejected because it could leave a conversation pointing at a
draft that does not exist.

Acceptable failure mode:

* orphan draft with `conversation_id` exists;
* conversation remains `open`;
* retry finds the existing order by `conversation_id`;
* retry marks conversation `draft_created`;
* retry returns `ALREADY_HAS_DRAFT`.

Concurrent draft creation is prevented by the unique non-null `conversation_id`
constraint on orders.

## 7. Parser and transcript boundary

M9.2C may render canonical conversation turns into a deterministic transcript
and pass it as `raw_message` to the existing `ParsingService`.

M9.2C must not change:

* `ParserInterface`;
* parser prompt;
* `PROMPT_VERSION`;
* parser output schema.

Recommended transcript shape:

```text
Customer message 1:
hola

Customer message 2:
tienen bandeja?

Customer message 3:
2 porfa
```

M9.2C must test current parser quality on representative multi-turn
transcripts. If quality is poor, stop M9.2C and create a separate parser
milestone with `PROMPT_VERSION` review. Do not introduce a transcript-aware
prompt inside M9.2.

## 8. Completeness rule

Validate before `OrderService.create_draft(...)`.

Complete enough means complete enough to create an operator-reviewable draft
only.

Required checks:

* parser returns `DraftOrderRequest`;
* at least one item;
* each item has `product_id`;
* each item has positive quantity;
* tenant-scoped active product context exists.

If incomplete:

* conversation stays `open`;
* no draft is created;
* no `resulting_order_id` is set.

## 9. After draft_created behavior

Later messages after `draft_created` append turns to preserve transcript
history.

They must not:

* mutate the existing draft;
* amend approved or confirmed orders;
* create another draft;
* trigger outbound behavior.

Return `ALREADY_HAS_DRAFT`.

Draft amendment is deferred.

## 10. Tenant scoping and architecture guard

The advancement service requires explicit `tenant_id` everywhere.

Product reads must go through:

```text
TenantScopedReadService.list_products(tenant_id=..., active_only=True)
```

No broad storage reads are allowed in the advancement service.

M9.2C must add the advancement service module to the AST broad-read guard's
enforced module set.

## 11. Required tests

M9.2B tests:

* nullable `conversation_id` exists on `DraftOrderRequest`;
* nullable `conversation_id` exists on `Order`;
* nullable `conversation_id` persists on orders;
* unique non-null `conversation_id` prevents duplicate orders;
* nullable `resulting_order_id` exists on `conversation_sessions`;
* `mark_draft_created(...)` sets status, resulting order id, and version;
* `mark_draft_created(...)` is idempotent with the same order id;
* `mark_draft_created(...)` conflicts with a different order id;
* conversation/order lookup finds an existing order by `conversation_id`;
* no `StorageInterface` signature change.

M9.2C tests:

* duplicate `message_sid` returns `DUPLICATE_MESSAGE`;
* duplicate `message_sid` does not call parser;
* duplicate `message_sid` does not create draft;
* incomplete parser result leaves conversation open;
* incomplete parser result creates no draft;
* complete transcript creates one draft;
* created draft carries `conversation_id`;
* successful creation marks conversation `draft_created`;
* orphan-draft crash-window retry returns `ALREADY_HAS_DRAFT`;
* orphan-draft crash-window retry creates no second draft;
* race test creates at most one draft;
* post-`draft_created` message appends turn;
* post-`draft_created` message returns `ALREADY_HAS_DRAFT`;
* post-`draft_created` message does not mutate draft;
* advancement service uses tenant-scoped product reads;
* advancement service is included in the AST broad-read guard;
* representative multi-turn parser quality tests pass or hard-stop the
  milestone.

## 12. Non-goals

* No webhook wiring.
* No UI.
* No bot replies.
* No auto-confirmation.
* No queue/worker.
* No payment gate.
* No inbound media/comprobante.
* No `OrderService` lifecycle change.
* No `StorageInterface` change.
* No parser prompt change.
* No `PROMPT_VERSION` change.
* No transcript-aware prompt in M9.2.
* No draft amendment after `draft_created`.
* No parse-status persistence.
* No cross-store transaction.
* No outbound behavior changes.
