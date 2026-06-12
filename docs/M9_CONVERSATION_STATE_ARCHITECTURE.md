# M9 Conversation State Architecture

Status: M9.0 design locked; M9.1 store foundation implemented; M9.2A
advancement seam refined; M9.2B draft-link persistence implemented; M9.2C-0
latest-session lookup implemented; M9.2C advancement service implemented;
M9.3A webhook wiring implemented; M9.4A conversation advancement hardening
tests implemented; M9.4B observability/read-model design refined; M9.4C
read-only conversation observation/read-model implemented; M9.4D persisted
conversation advancement observability implemented; M9.4E idle-boundary
design recorded and runtime expiry deferred. M9 closed.

Baseline: `6bd4c40 docs(outbound): close retry attempt limit`

M9 introduces conversation state as a front-end intake stage for WhatsApp
ordering. It lets multiple inbound customer messages accumulate into one
operator-reviewable draft order while preserving the already-proven downstream
lifecycle:

```text
draft -> approved -> confirmed -> atomic inventory commit -> outbound acknowledgement
```

Conversation state must not leak into the parser, `StorageInterface`,
`OrderService` lifecycle, confirmation transaction, or outbound/provider
behavior.

M9.2A seam decision:

* conversation-origin drafts will carry nullable `conversation_id`;
* orders will enforce one row per non-null `conversation_id`;
* `conversation_sessions` will gain nullable `resulting_order_id`;
* advancement will create the draft first, then mark the conversation
  `draft_created`;
* if the process crashes between those writes, retry recovers by finding the
  existing order by `conversation_id` and returns `ALREADY_HAS_DRAFT`;
* the detailed design is in
  `docs/M9_2A_CONVERSATION_ADVANCEMENT_SERVICE_DESIGN.md`.

## 1. Pre-flight findings

Current inbound flow is synchronous and assumes one complete inbound message.

The FastAPI webhook lives in `src/duna_orders/web/app.py` at
`POST /webhooks/twilio/whatsapp`.

Current flow:

1. Read the Twilio form body.
2. Validate `X-Twilio-Signature` against configured
   `twilio_webhook_public_url`.
3. Require `MessageSid`.
4. Read `Body`, `From`, and configured `webhook_tenant_id`.
5. Call `PostgresProcessedMessageStore.try_record_message(...)`.
6. If the `MessageSid` is already recorded, return `200` without parsing or
   order creation.
7. If the body is non-empty, call
   `create_draft_from_inbound_message(...)`.
8. If a draft is created, call
   `PostgresProcessedMessageStore.mark_order_created(...)`.
9. Return `200`.

The current draft creation seam lives in `src/duna_orders/web/inbound.py`.

`create_draft_from_inbound_message(...)`:

* trims the inbound body;
* reads active products through
  `TenantScopedReadService.list_products(tenant_id=..., active_only=True)`;
* calls `ParsingService(parser, storage).parse(...)`;
* takes `ParseResult.request`, a `DraftOrderRequest`;
* normalizes Twilio `From` into the customer phone;
* overwrites parsed `tenant_id`, `raw_message`, `customer_phone`, and item
  `tenant_id` from trusted webhook context;
* calls `OrderService(...).create_draft(request)`.

`OrderService.create_draft(...)` remains the existing draft boundary. It
requires positive items, validates product existence and active status,
computes totals, creates or links the customer by phone, creates
`Order(status="draft")`, and optionally records the initial lifecycle
transition.

`processed_messages` is a Postgres-only idempotency store. `message_sid` is the
primary key. Insert-first behavior returns `True` for a new message and
`False` for a duplicate. Conversation advancement must compose with this:
state may advance only after a new `MessageSid` is durably recorded.

Outbound acknowledgement persistence is the closest precedent. It uses a
narrow protocol and Postgres implementation outside `StorageInterface`, with
service-layer orchestration above it. Conversation state has the same shape:
runtime-specific durable state that should not expand the general
product/order storage contract.

Tenant scoping currently comes from configured `webhook_tenant_id` and flows
through processed messages, parser product context, draft request
normalization, and created draft orders. M9 must keep explicit `tenant_id`
through all conversation store and service APIs.

The parser contract is explicitly stateless:

```text
parse(raw_message, products) -> ParseResult
```

`ParsingService` logs `PROMPT_VERSION`, but does not own conversation state.
M9.0 and M9.1 do not change `ParserInterface` or `PROMPT_VERSION`.

## 2. Conversation domain object and session boundary

A conversation is a tenant-scoped customer intake session that records inbound
turns until the system can produce one operator-reviewable draft order.

Recommended session key for M9:

```text
tenant_id + customer_phone
```

M9 uses one open conversation per `(tenant_id, customer_phone)` within an idle
window. If the latest conversation for that tenant/customer is idle beyond the
boundary, the next inbound message starts a new conversation.

Recommended idle boundary for first implementation:

```text
4 hours since last_message_at
```

This is long enough for real ordering hesitation and short enough to avoid
merging separate meal-period intents.

Minimal conversation session fields:

* `conversation_id`;
* `tenant_id`;
* `customer_phone`;
* `status`;
* `opened_at`;
* `last_message_at`;
* `version`;

M9.1 implemented only those persistence fields. Orchestration-owned fields such
as `resulting_order_id`, `latest_parse_status`, and `latest_parse_error` are
deferred to M9.2 or later.

Recommended session statuses:

* `open`;
* `draft_created`;
* `expired`;
* `failed`.

Minimal conversation turn fields:

* `turn_id`;
* `conversation_id`;
* `tenant_id`;
* `message_sid`;
* `from_number`;
* `body`;
* `received_at`;
* `sequence_number`.

Conversation turns are canonical. M9.1 does not store `accumulated_text`.
Future transcript text must be reconstructed from ordered turns, or treated as
a derived cache only.

## 3. Persistence boundary

Decision: use a narrow `ConversationStateStore` protocol outside
`StorageInterface`.

Do not extend `StorageInterface` in M9.

Reasons:

* `StorageInterface` owns current product, customer, order, stock movement, and
  parse-log persistence.
* Conversation state is runtime orchestration state.
* Postgres is already the target for webhook idempotency and outbound
  acknowledgement idempotency.
* Google Sheets cannot be the source of truth for concurrent conversational
  state.
* Forcing conversation methods into `StorageInterface` would create false
  expectations for Memory and Sheets backends.

Store responsibilities:

* find or create an open conversation for `(tenant_id, customer_phone)`;
* append a turn idempotently by `message_sid`;
* preserve turn order;
* enforce `message_sid` uniqueness;
* protect same-customer concurrent updates through optimistic versioning or
  transaction-level locking;
* expose read methods needed later by operator diagnostics.

M9.1 store responsibilities are narrower:

* expose exactly `get_or_create_open_session(...)`,
  `append_turn_if_new(...)`, `list_turns(...)`, and `get_session(...)`;
* write only `open` sessions;
* store customer phone exactly as received from the caller;
* avoid idle-expiry policy, parser calls, draft creation, webhook wiring, and
  UI behavior.

M9.1 deliberately does not expose `mark_draft_created(...)` or
`expire_session(...)`; those require orchestration state and are deferred.

Service responsibilities:

* receive trusted webhook context after processed-message dedup succeeds;
* normalize customer identity;
* append the inbound turn;
* render a deterministic transcript from canonical turns;
* call existing `ParsingService`;
* apply the deterministic operator-reviewable draft completeness rule;
* call `OrderService.create_draft(...)` only when complete enough;
* link the conversation and processed message to the resulting draft.

The store persists. The service orchestrates.

## 4. Advancement trigger

M9 first implementation advances conversation only on a unique inbound
customer message.

No queue, worker, operator action advancement, timeout sweeper, or outbound
reply is part of M9.1-M9.3.

The first implementation remains synchronous:

1. webhook validates Twilio;
2. webhook records `MessageSid` through `processed_messages`;
3. only a newly recorded message advances conversation;
4. advancement may create an operator-reviewable draft;
5. webhook returns `200`.

This keeps M9 focused on the state seam and avoids introducing queue semantics
before the model is proven.

## 5. The draft seam

The conversation layer produces the same `DraftOrderRequest` that the existing
system already consumes.

It does not produce an approved order. It does not confirm an order. It does
not send an acknowledgement. It does not commit inventory.

Complete enough means complete enough to create an operator-reviewable draft.
Operator review remains the safety boundary.

Complete enough for first implementation is deterministic:

* parser returns a valid `DraftOrderRequest`;
* request has at least one item;
* every item has a positive quantity;
* every item has a product id;
* `OrderService.create_draft(...)` accepts the request without domain/service
  errors.

The conversation advancement service decides whether the parsed result is
complete enough for an operator-reviewable draft. The parser proposes
structure; it does not own completion policy.

When complete enough:

1. service calls existing `OrderService.create_draft(...)`;
2. created order has `status="draft"`;
3. conversation status becomes `draft_created`;
4. `resulting_order_id` is stored on the conversation;
5. `processed_messages.resulting_order_id` is linked for the triggering
   `MessageSid`.

After `draft_created`, M9 first implementation must not automatically mutate
approved or confirmed orders.

M9 first implementation also does not automatically amend an existing draft
after `draft_created`. Later customer turns after a draft is created should be
recorded or surfaced for follow-up according to a later design, but they must
not silently alter existing drafts, approved orders, confirmed orders,
inventory, or outbound acknowledgements.

Deferred milestone:

* amend existing draft from later customer turns.

The downstream lifecycle remains untouched:

```text
draft -> approved -> confirmed -> atomic inventory commit -> outbound acknowledgement
```

## 6. Parser-stays-stateless confirmation

The parser remains stateless in M9.

M9.0 and M9.1 do not change:

* `ParserInterface`;
* `PROMPT_VERSION`;
* provider parser implementation;
* parser ownership of storage;
* parser output schema.

The conversation service may render multiple turns into a deterministic
transcript string and pass that string as `raw_message` to the existing
`ParsingService.parse(...)`.

The transcript renderer is service-owned, not parser-owned.

Initial deterministic transcript format should be simple and stable, for
example:

```text
Customer message 1:
hola

Customer message 2:
tienen bandeja?

Customer message 3:
2 porfa
```

This gives the current parser more context without changing its interface.

If future work changes the parser prompt to explicitly understand transcript
format, that must be a separate parser milestone and may require
`PROMPT_VERSION` review. It is not part of M9.0 or M9.1.

## 7. Idempotency composition

`processed_messages` remains the first idempotency gate.

Conversation advancement may run only when:

```text
PostgresProcessedMessageStore.try_record_message(...) == True
```

Duplicate `MessageSid` behavior:

* no conversation turn append;
* no session mutation;
* no parser call;
* no draft creation;
* no duplicate `processed_messages.resulting_order_id` linkage.

The conversation turn store should also enforce `message_sid` uniqueness as a
second defensive guard. That guard composes with `processed_messages`; it does
not replace it.

Implementation tests must prove:

* duplicate `MessageSid` does not append duplicate turns;
* duplicate `MessageSid` does not create duplicate drafts;
* close-arriving turns for the same tenant/customer do not create duplicate
  drafts;
* racing turn advancement is handled through optimistic versioning or
  transaction-level locking.

## 8. Tenant scoping

Conversation sessions and turns are keyed by `tenant_id`.

All conversation store and service methods require explicit `tenant_id`.

Runtime product context continues through:

```text
TenantScopedReadService.list_products(tenant_id=..., active_only=True)
```

No direct broad product, customer, or order reads should be introduced in the
conversation runtime path.

Tenant identity is not inferred from customer text. For current M9 work, tenant
identity continues to come from webhook configuration. Future tenant-channel
binding can replace that source without changing conversation semantics.

## 9. Observability hooks

M9 should persist enough information for a later operator conversation view,
without building UI in this milestone.

Required observability hooks:

* append-only ordered turns;
* raw inbound body per turn;
* `message_sid` per turn;
* session status;
* session version;
* `resulting_order_id` when a draft is created;
* latest parse status or safe parse error classification;
* timestamps for open and latest activity.

Later UI should be able to show:

* conversation-so-far;
* current accumulated draft/order link;
* whether the conversation is still open, failed, expired, or already produced
  a draft.

No UI is part of M9.0-M9.3.

## 10. Concurrency and versioning

The conversation store must protect against close-arriving messages for the
same tenant/customer.

Required protections:

* unique turn constraint on `message_sid`;
* ordered turn sequencing per conversation;
* optimistic session `version` checks or transaction-level row locking when
  appending a turn and updating session state;
* draft creation guard so one conversation cannot create multiple drafts under
  races.

Recommended first implementation:

* claim or lock the active conversation row for `(tenant_id, customer_phone)`
  while appending the turn and advancing the session;
* increment `version` on every successful turn append/state update;
* only transition `open -> draft_created` if the session is still open and has
  no `resulting_order_id`;
* treat second draft-creation attempts as suppressed/idempotent, not as a
  second order creation.

## 11. Implementation split

### M9.0 - Design lock

Scope:

* create this architecture document;
* update `DECISIONS.md`;
* update `ROADMAP.md`;
* update `CHANGELOG.md`.

No code, tests, migrations, commits, or pushes.

### M9.1 - Store foundation only

Status: implemented in `e25634a feat(m9): add conversation state store`.

Scope completed:

* Added domain/state dataclasses for conversation session and turn.
* Added narrow `ConversationStateStore` protocol outside `StorageInterface`.
* Added `PostgresConversationStateStore`.
* Added Postgres tables for sessions and turns.
* Added append-turn idempotency by tenant-scoped `message_sid`.
* Added session lookup by `tenant_id` and `customer_phone`.
* Added a partial unique index enforcing one open session per tenant/customer.
* Added transaction-level locking for append sequencing and session timestamp
  updates.
* Added store-only tests, metadata guard coverage, and live Postgres
  constraint/concurrency coverage.

Explicitly excluded:

* parser calls;
* draft creation;
* webhook wiring;
* UI;
* outbound replies;
* `StorageInterface` changes.
* four-hour expiry policy in the store;
* `resulting_order_id`, parse-status fields, `mark_draft_created(...)`, and
  `expire_session(...)`.

### M9.2 - Advancement service

Scope:

* append turn;
* render deterministic transcript from canonical turns;
* call existing `ParsingService`;
* apply deterministic operator-reviewable draft completeness rule;
* create draft through existing `OrderService.create_draft(...)`;
* mark conversation `draft_created`;
* link the triggering processed message to the resulting draft.

Explicitly excluded:

* parser prompt change;
* `PROMPT_VERSION` change;
* automatic draft amendment after `draft_created`;
* outbound replies;
* UI.

### M9.3A - Webhook wiring

Status: implemented in `1cf5b6a feat(m9): wire webhook to conversation
advancement`.

Scope completed:

* replaced the direct `create_draft_from_inbound_message(...)` call with
  `ConversationAdvancementService.advance(...)`;
* preserved Twilio signature validation as the first gate, before any side
  effects;
* preserved `processed_messages` first-gate `MessageSid` idempotency;
* preserved webhook `200` response behavior for all five advancement
  outcomes, with no outbound reply;
* preserved tenant-scoped product reads (via `ParsingService` /
  `TenantScopedReadService` inside the advancement service);
* preserved `processed_messages.resulting_order_id` linking via
  `mark_order_created(...)`;
* added required-field validation for `From` (`400` on empty/missing),
  mirroring the existing `MessageSid` check.

Explicitly excluded:

* queue/worker;
* outbound conversational replies;
* new provider behavior;
* UI, auto-confirmation, payment gate, inbound media;
* session expiry / draft amendment;
* `StorageInterface` and schema/migration changes.

Deferred follow-up:

* `create_draft_from_inbound_message(...)` and `web/inbound.py` are now
  dead/unreferenced and left in place for a later cleanup slice.

### M9.4 - Tests and observability hardening

Scope:

* duplicate `MessageSid` coverage;
* racing same-customer turn coverage;
* tenant isolation coverage;
* idle-boundary coverage;
* draft-created single-output coverage;
* observability/read-model coverage for later operator UI.

No UI build.

#### M9.4A - Conversation advancement hardening tests

Status: implemented in `b5f38fe test(m9): harden conversation advancement
wiring`.

Scope completed:

* invalid signature does not record `processed_messages` or call
  `ConversationAdvancementService.advance(...)`;
* missing `From` does not record `processed_messages`;
* a post-`draft_created` follow-up message does not mutate the existing
  draft;
* a duplicate follow-up `MessageSid` after `draft_created` remains
  idempotent;
* the same customer phone number across tenants remains isolated through the
  webhook path;
* `live_postgres` same-customer concurrent advancement converges to one
  `resulting_order_id`;
* `src/duna_orders/web/app.py` is covered by the broad-read architecture
  guard.

#### M9.4B - Conversation observability/read-model design

Status: design refined in
`docs/M9_4B_CONVERSATION_OBSERVABILITY_READ_MODEL_DESIGN.md`.

Scope completed:

* documented existing conversation observability (session and turn fields,
  `ConversationOrderLookup`) available today with no schema change;
* split remaining observability work into M9.4C (read-only
  `ConversationObservationReads`/`PostgresConversationObservationReads`
  snapshot read-model, no schema change, outside `StorageInterface`) and
  M9.4D (persisted `latest_advancement_outcome` /
  `latest_parse_error_category` hooks via `record_advancement_attempt(...)`,
  requires migration and a safe-category policy for parse errors);
* confirmed `opened_at`/`last_message_at` already support read-time idle
  visibility; idle-boundary behavior/policy remains a separate deferred
  slice.

#### M9.4C - Conversation observation read-model

Status: implemented in `bc2de4a feat(m9): add conversation observation read
model`.

Scope completed:

* `ConversationObservationReads` protocol and
  `PostgresConversationObservationReads` in
  `src/duna_orders/storage/conversation_observation.py`, outside
  `StorageInterface`;
* `ConversationObservationItem` / `ConversationObservationDiagnostics` /
  `ConversationObservationSnapshot` frozen dataclasses;
* `get_conversation_observation_snapshot(*, tenant_id, now,
  idle_threshold=DEFAULT_IDLE_THRESHOLD)`, a tenant-scoped, no-N+1 snapshot
  read over `conversation_sessions` and `conversation_turns` via
  `session_scope(...)`;
* read-time fields `turn_count`, `latest_message_sid`,
  `latest_body_preview` (truncated, with `""` vs `None` preserved),
  `linked_order_id` (from `resulting_order_id`), `has_draft`, `is_idle`, and
  `needs_operator_attention`;
* diagnostics counts `total_count`, `open_count`, `draft_created_count`,
  `idle_count`, `needs_attention_count`;
* sessions with zero turns included with `turn_count=0` and `None`
  latest-turn fields;
* 17 local SQLite-backed tests in `tests/test_conversation_observation.py`
  (no `live_postgres`).

Explicitly excluded:

* no schema/migration changes;
* no changes to `ConversationStateStore`,
  `ConversationAdvancementService`, or `web/app.py`;
* no UI / operator page;
* no `latest_advancement_outcome`, `latest_parse_error_category`, or
  `latest_parse_status` (M9.4D);
* no idle/session-expiry behavior - `is_idle` remains a read-time-only
  comparison, not a session-boundary policy;
* no `StorageInterface` changes;
* `live_sheets` not run.

#### M9.4D - Persisted conversation advancement observability

Status: implemented in `1b33d8a feat(m9): add conversation advancement
observability storage` and `eb4c235 feat(m9): record conversation
advancement observability`.

Scope completed:

* migration `11605e30520d` adds nullable `conversation_sessions` columns
  `latest_advancement_outcome` and `latest_parse_error_category`;
* `ConversationSessionRow`, `ConversationSession`, and `_session_from_row`
  updated for both fields; `ConversationObservationItem` exposes both;
* `ADVANCEMENT_OUTCOME_VALUES` and `PARSE_ERROR_CATEGORY_VALUES =
  frozenset({"PARSER_ERROR"})` defined module-level in
  `conversation_state.py`, independent of
  `ConversationAdvancementOutcome`;
* `record_advancement_attempt(*, tenant_id, conversation_id, outcome,
  parse_error_category=None) -> ConversationSession` added to
  `ConversationStateStore` / `PostgresConversationStateStore`, outside
  `StorageInterface`; uses a tenant-scoped `SELECT ... FOR UPDATE`,
  validates outcome/category, sets both fields, increments `version`,
  updates `updated_at`, and returns the refreshed `ConversationSession`;
* `ConversationAdvancementService.advance(...)` restructured to a single
  return boundary; `_record_outcome(...)` records best-effort
  observability via `record_advancement_attempt` for every non-duplicate
  outcome;
* outcome -> category mapping: `TURN_APPENDED_INCOMPLETE ->
  "PARSER_ERROR"`; `PARSE_INCOMPLETE`, `DRAFT_CREATED`, `ALREADY_HAS_DRAFT`
  -> `None` (category cleared);
* `ALEMBIC_HEAD_REVISION = "11605e30520d"` updated in
  `tests/test_smoke_preflight.py`.

Safety conclusions:

* observability is best-effort telemetry: `_record_outcome` never changes
  the caller-visible `ConversationAdvancementResult`;
* recording failures are caught, logged via `logger.warning(...,
  exc_info=True)`, and the original result is returned unchanged;
* `DUPLICATE_MESSAGE` intentionally is not recorded and does not mutate
  session observability, preserving the "no session mutation" guarantee
  from `docs/M9_2A_CONVERSATION_ADVANCEMENT_SERVICE_DESIGN.md` section 6;
* `ALREADY_HAS_DRAFT` is recorded for orphan-draft recovery,
  post-`DRAFT_CREATED` follow-up, and create-draft-conflict recovery
  paths;
* raw parser/LLM error text is never persisted - only the safe
  `PARSER_ERROR` category;
* `latest_parse_status` was intentionally not added.

Explicitly excluded:

* no UI / operator page;
* no outbound replies;
* no idle/session-expiry behavior;
* no draft amendment;
* no `web/inbound.py` cleanup;
* no parser prompt or `PROMPT_VERSION` changes;
* no `StorageInterface` changes;
* `live_sheets` not run.

#### M9.4E - Idle-boundary design and deferral

Status: design recorded in `docs/M9_4E_IDLE_BOUNDARY_DESIGN.md`; runtime
idle-boundary expiry deferred.

Scope completed:

* documented the intended idle policy: idle boundary =
  `received_at - open_session.last_message_at > DEFAULT_IDLE_THRESHOLD`
  (default 4 hours), applying only to `status="open"`; `draft_created`
  never auto-expires; `expired`/`failed` remain terminal/non-routable; a
  post-idle message starts a brand-new conversation with
  `sequence_number=1` and no inherited transcript context;
* documented the required cross-session invariant for
  `(tenant_id, customer_phone)`: at most one routable session; a successful
  `create_draft(...)` must drive its producing session to
  `draft_created`/`resulting_order_id`; idle expiry must not let a new
  `open` session win over an existing `draft_created` session for the same
  customer; `mark_draft_created(...)` must never silently no-op into an
  expired/unlinked row; `get_latest_session_for_customer(...)` must keep a
  `draft_created` session as the routing answer over a later `open` session,
  or the competing `open` session must not be created;
* added
  `tests/test_conversation_state_store.py::test_draft_created_session_remains_latest_over_later_open_session_for_customer`
  as a `strict=True` xfail acceptance test reproducing the invalid terminal
  state (`old=draft_created`, `new=open` and `latest`) for a future
  implementation;
* documented why a runtime implementation attempt was reverted: each
  `PostgresConversationStateStore` method opens its own
  `session_scope`/transaction, so a transaction-scoped
  `pg_advisory_xact_lock` inside one method cannot protect the
  `_route_session -> append_turn_if_new -> parse -> create_draft ->
  mark_draft_created -> record_advancement_attempt` lifecycle that spans
  multiple store calls, and a naive session-scoped service-level lock can
  self-deadlock against those inner calls' own connections;
* documented the future prerequisite: a lifecycle-spanning, per-customer
  unit of work for conversation advancement that also resolves how to bound
  serialization around parser/LLM/network latency without pinning a
  database connection for the duration of an external API call.

Explicitly excluded:

* no runtime idle-boundary expiry;
* no `status="expired"` writes by runtime code;
* no migration;
* no `StorageInterface` changes;
* no UI;
* `live_sheets` not run.

See `docs/M9_4E_IDLE_BOUNDARY_DESIGN.md` for the full design, including the
required invariant and the acceptance-test walkthrough.

Remaining M9.4 work: none. M9.4 is closed.

## 12. What M9 will and will not touch

M9 will touch, after M9.0 design approval:

* conversation state models;
* narrow conversation persistence protocol;
* Postgres conversation persistence;
* conversation advancement service;
* inbound webhook orchestration seam;
* tests for idempotency, tenant scoping, races, and draft output.

M9 will not touch:

* bot question policy;
* outbound conversational replies;
* auto-confirmation;
* payment gate;
* inbound media or comprobante handling;
* queue/worker processing;
* UI build;
* `StorageInterface`;
* parser prompt;
* `PROMPT_VERSION`;
* `OrderService` lifecycle rules;
* confirmation transaction;
* outbound acknowledgement service;
* outbound provider adapters.

## 13. Proposed DECISIONS.md entries

Record these decisions with M9.0:

* Conversation state is a front-end intake stage that produces existing
  operator-reviewable draft orders.
* Conversation persistence uses a narrow Postgres-backed protocol outside
  `StorageInterface`.
* Conversation turns are canonical; any accumulated transcript field is a
  derived cache.
* `processed_messages.MessageSid` remains the first idempotency gate.
* Parser remains stateless; M9.0 and M9.1 do not change `ParserInterface` or
  `PROMPT_VERSION`.
* M9 first implementation does not automatically amend drafts after
  `draft_created`.
* Downstream lifecycle remains unchanged from draft review through outbound
  acknowledgement.
