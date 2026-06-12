# Roadmap

This roadmap tracks future work for Duna Orders and keeps a lightweight milestone archive.

Detailed completed work belongs in `CHANGELOG.md`. This file only keeps milestone-level summaries, deferred follow-ups, and next-candidate direction.

## High priority

## M9 - Conversation state architecture

Status: closed. M9.3A closed; M9.4A closed; M9.4B closed; M9.4C closed;
M9.4D closed; M9.4E closed (design/deferral). Runtime idle-boundary expiry
is deferred to a future milestone (lifecycle-spanning unit of work for
conversation advancement).

M9 introduces conversation state as the next real WhatsApp capability. The goal
is to support customers who order across multiple messages while preserving the
existing downstream lifecycle:

```text
draft -> approved -> confirmed -> atomic inventory commit -> outbound acknowledgement
```

### M9.0 - Conversation state architecture design lock

Status: closed.

Scope completed:

* Added `docs/M9_CONVERSATION_STATE_ARCHITECTURE.md`.
* Locked the conversation seam as a front-end intake stage that produces the
  existing operator-reviewable draft.
* Chose a narrow `ConversationStateStore` protocol outside `StorageInterface`.
* Kept parser statefulness, `ParserInterface`, and `PROMPT_VERSION` unchanged.
* Kept `OrderService` lifecycle, confirmation transaction, and outbound/provider
  behavior unchanged.
* Defined `processed_messages.MessageSid` as the first idempotency gate for
  conversation advancement.
* Deferred automatic draft amendment after `draft_created`.

### M9.1 - Conversation store foundation

Status: closed.

Scope completed:

* Added conversation session and turn state models.
* Added a narrow `ConversationStateStore` protocol outside `StorageInterface`.
* Added `PostgresConversationStateStore`.
* Added Postgres tables for sessions and turns.
* Added append-turn idempotency by tenant-scoped `message_sid`.
* Added one-open-session protection through a partial unique index on
  `(tenant_id, customer_phone) WHERE status = 'open'`.
* Added transaction-level locking for append sequencing and session timestamp
  updates.
* Added store-only tests and live Postgres constraint/concurrency coverage.
* Updated Postgres metadata guards and smoke preflight Alembic head expectation.

Explicitly excluded:

* Parser calls.
* Draft creation.
* Webhook wiring.
* UI.
* Outbound conversational replies.
* Four-hour expiry policy in the store.
* `resulting_order_id`, parse-status fields, `mark_draft_created(...)`, and
  `expire_session(...)`.

### M9.2A - Conversation advancement service design refinement

Status: closed.

Scope completed:

* Added `docs/M9_2A_CONVERSATION_ADVANCEMENT_SERVICE_DESIGN.md`.
* Locked the orphan-draft idempotency decision:
  conversation-origin orders carry nullable `conversation_id`, and orders have
  a unique non-null `conversation_id` constraint.
* Chose a narrow Postgres-backed conversation/order lookup helper outside
  `StorageInterface`.
* Split M9.2 into schema/domain/persistence first, then service orchestration.

### M9.2B - Conversation draft link persistence

Status: closed.

Scope completed:

* Added nullable `conversation_id` to `DraftOrderRequest` and `Order`.
* `OrderService.create_draft` carries `request.conversation_id` into the
  created draft `Order`.
* Added nullable `orders.conversation_id` in Postgres.
* Added a one-order-row-per-non-null-`conversation_id` constraint/index,
  global and not status-dependent; multiple `NULL` `conversation_id` orders
  remain allowed.
* Added a `tenant_id` + `conversation_id` lookup index.
* Added nullable `resulting_order_id` to `conversation_sessions`.
* Added `mark_draft_created(tenant_id, conversation_id, order_id)`.
* Added `PostgresConversationOrderLookup`, a narrow Postgres-backed lookup by
  `conversation_id` outside `StorageInterface`.
* Carried nullable `conversation_id` across Postgres, memory, and
  Sheets-backed order paths and updated schema constants and tests.
* Updated the Alembic head expectation to `d6e7f8a9b0c1`.

Explicitly excluded:

* Parser calls.
* Service orchestration.
* Webhook wiring.
* UI.
* Draft advancement flow.
* Header migration for existing live Sheets spreadsheets predating the new
  `conversation_id` column; `live_sheets` was not run.

### M9.2C-0 - Latest customer conversation lookup

Status: closed.

Scope completed:

* Added `ConversationStateStore.get_latest_session_for_customer(tenant_id,
  customer_phone)`.
* Implemented it in `PostgresConversationStateStore`.
* Requires explicit `tenant_id` and matches `customer_phone` exactly as
  stored, with no normalization.
* Returns the latest `ConversationSession` for a tenant/customer regardless of
  status, ordered deterministically by `last_message_at DESC, updated_at DESC,
  opened_at DESC, conversation_id DESC`.
* Returns `None` if no matching session exists.
* Read-only: does not create sessions, append turns, mark `draft_created`,
  call the parser, call `OrderService`, or touch `StorageInterface`.

Reason:

* M9.2C must not call `get_or_create_open_session` blindly after a customer's
  latest session is `draft_created`. A post-`draft_created` message must
  attach to that existing latest session and return `ALREADY_HAS_DRAFT`, not
  create a new open session and not create a second draft.

Explicitly excluded:

* M9.2C service orchestration.
* True new-order session boundary / idle-expiry policy.
* Webhook, UI, and outbound.

### M9.2C - Conversation advancement service

Status: closed.

Scope completed:

* Added `src/duna_orders/services/conversation_advancement.py` with
  `ConversationAdvancementService.advance(...)`, the
  `ConversationAdvancementOutcome` enum (`TURN_APPENDED_INCOMPLETE`,
  `PARSE_INCOMPLETE`, `DRAFT_CREATED`, `ALREADY_HAS_DRAFT`,
  `DUPLICATE_MESSAGE`), and `ConversationAdvancementResult`.
* Routing uses `get_latest_session_for_customer(tenant_id, from_number)`
  before `get_or_create_open_session(...)`. An `open` latest session is
  reused; a `draft_created` latest session is reused so post-draft messages
  attach to it and return `ALREADY_HAS_DRAFT` instead of opening a new
  session or creating a second draft. Any other future session status raises
  `NotImplementedError`.
* Renders a deterministic transcript from canonical conversation turns and
  calls existing `ParsingService` with it as `raw_message`.
* Fetches products through `TenantScopedReadService.list_products(tenant_id=...,
  active_only=True)`.
* Applies the deterministic completeness rule (at least one item, each item
  has `product_id` and positive quantity, each `product_id` exists in the
  tenant-scoped active product list) before draft creation.
* Recovers orphan drafts via
  `ConversationOrderLookup.get_order_by_conversation_id(...)` and
  `mark_draft_created(...)`, including recovery from an `IntegrityError` on
  the unique non-null `conversation_id` constraint during
  `OrderService.create_draft(...)`.
* Creates drafts through existing `OrderService.create_draft(...)` with
  `request.conversation_id` set, then marks the conversation
  `draft_created`.
* Added `tests/test_conversation_advancement.py` (9 tests) and added the new
  module to the architecture boundary guard.

Explicitly excluded:

* Webhook wiring, UI, bot replies, and outbound changes.
* `ParserInterface`, parser prompt, and `PROMPT_VERSION` changes.
* `StorageInterface` signature changes.
* `OrderService` lifecycle/state transition and confirmation transaction
  changes.
* Draft amendment after `draft_created`.
* Session expiry / new-order boundary policy.
* Queue/worker/callbacks, payment gate, and inbound media.
* `live_sheets` was not run.

### M9.3A - Webhook wiring

Status: closed.

Scope completed:

* `POST /webhooks/twilio/whatsapp` calls
  `ConversationAdvancementService.advance(...)` instead of
  `create_draft_from_inbound_message(...)`.
* Twilio signature validation remains the first gate, before any side
  effects; an invalid signature returns `403` without calling the
  advancement service or creating conversation state.
* `processed_messages` `MessageSid` idempotency remains the first
  business/persistence gate; a duplicate `MessageSid` returns `200` without
  calling the advancement service or the parser.
* A new `MessageSid` calls `advance(...)` exactly once, after the idempotency
  pass.
* All five outcomes (`TURN_APPENDED_INCOMPLETE`, `PARSE_INCOMPLETE`,
  `DRAFT_CREATED`, `ALREADY_HAS_DRAFT`, `DUPLICATE_MESSAGE`) return `200` with
  no outbound reply.
* `processed_messages.resulting_order_id` linking is preserved via
  `mark_order_created(...)` when `advance(...)` returns a
  `resulting_order_id`.
* Added required-field validation for `From` (`400` on empty/missing),
  mirroring the existing `MessageSid` check.
* Rewrote `tests/test_web_twilio_webhook.py` (23 tests).
* Implemented in `1cf5b6a feat(m9): wire webhook to conversation
  advancement`.

Explicitly excluded:

* Queue/worker.
* Outbound conversational replies.
* New provider behavior.
* UI, auto-confirmation, payment gate, and inbound media.
* Session expiry / draft amendment.
* `StorageInterface` and schema/migration changes.
* `live_sheets` was not run.

Deferred follow-up:

* `create_draft_from_inbound_message(...)` and `web/inbound.py` are now
  dead/unreferenced and left in place for a later cleanup slice.

### M9.4 - Tests and observability hardening

Status: closed. M9.4A closed; M9.4B closed; M9.4C closed; M9.4D closed;
M9.4E closed (design/deferral).

Scope:

* Prove duplicate and racing turns do not create duplicate drafts.
* Prove tenant isolation and idle-boundary behavior.
* Add observability hooks for a later operator conversation view.

### M9.4A - Conversation advancement hardening tests

Status: closed.

Scope completed:

* Invalid Twilio signature does not record a `processed_messages` row or call
  the advancement service.
* Missing `From` does not record a `processed_messages` row.
* A post-`draft_created` follow-up message does not mutate the existing draft
  order.
* A duplicate follow-up `MessageSid` after `draft_created` remains idempotent
  (no reprocessing).
* The same customer phone number across two tenants remains isolated
  (separate conversations and separate drafts) through the webhook path.
* `live_postgres` concurrent advancement for the same customer converges to
  one `resulting_order_id`.
* `src/duna_orders/web/app.py` is added to the broad-read architecture guard
  (`ENFORCED_RUNTIME_READ_MODULES`).

Implemented in `b5f38fe test(m9): harden conversation advancement wiring`.

### M9.4B - Conversation observability/read-model design

Status: closed.

Scope completed:

* Added `docs/M9_4B_CONVERSATION_OBSERVABILITY_READ_MODEL_DESIGN.md`.
* Documented existing conversation observability available today with no
  schema change.
* Split remaining observability work into M9.4C (read-only
  `ConversationObservationReads`/`PostgresConversationObservationReads`
  read-model, no schema change) and M9.4D (persisted
  `latest_advancement_outcome`/`latest_parse_error_category` hooks via
  `record_advancement_attempt(...)`, requires migration).
* Confirmed idle-boundary visibility needs no new persisted field;
  idle-boundary policy remains a separate deferred slice.

### M9.4C - Conversation observation read-model

Status: closed.

Scope completed:

* Added `src/duna_orders/storage/conversation_observation.py` with the
  `ConversationObservationReads` protocol and
  `PostgresConversationObservationReads`, outside `StorageInterface`.
* Added `ConversationObservationItem` / `ConversationObservationDiagnostics`
  / `ConversationObservationSnapshot` frozen dataclasses, mirroring
  `InboundDraftReviewItem` / `InboundReviewDiagnostics` /
  `InboundReviewSnapshot`.
* Added `get_conversation_observation_snapshot(*, tenant_id, now,
  idle_threshold=DEFAULT_IDLE_THRESHOLD)`, a tenant-scoped snapshot read
  built from `conversation_sessions` and `conversation_turns` via three
  portable `select(...)` queries through `session_scope(...)` (no N+1, no
  Postgres-only `DISTINCT ON`).
* Computed read-time fields per item: `turn_count`, `latest_message_sid`,
  `latest_body_preview` (truncated to 160 characters, preserving an empty
  `""` body separately from "no turns" `None`), `linked_order_id` (from
  `resulting_order_id`), `has_draft`, `is_idle` (`now - last_message_at >
  idle_threshold`, default four hours), and `needs_operator_attention`
  (`status == "open" and linked_order_id is None and (turn_count >=
  ATTENTION_TURN_THRESHOLD or is_idle)`, with `ATTENTION_TURN_THRESHOLD = 3`).
* Added diagnostics counts: `total_count`, `open_count`,
  `draft_created_count`, `idle_count`, `needs_attention_count`.
* Sessions with zero turns are included with `turn_count=0`,
  `latest_message_sid=None`, `latest_body_preview=None`.
* Added `tests/test_conversation_observation.py` (17 local SQLite-backed
  tests; no `live_postgres`).
* Implemented in `bc2de4a feat(m9): add conversation observation read
  model`.

Explicitly excluded:

* No schema/migration changes.
* No changes to `ConversationStateStore`, `ConversationAdvancementService`,
  or `web/app.py`.
* No UI / operator page.
* No `latest_advancement_outcome`, `latest_parse_error_category`, or
  `latest_parse_status` (deferred to M9.4D).
* No idle/session-expiry behavior; `is_idle` is a read-time-only comparison,
  not a session-boundary policy.
* No `StorageInterface` changes.
* `live_sheets` was not run.

### M9.4D - Persisted conversation advancement observability

Status: closed.

Scope completed:

* Added migration `11605e30520d`, adding nullable `conversation_sessions`
  columns `latest_advancement_outcome` and `latest_parse_error_category`.
* Updated `ConversationSessionRow`, `ConversationSession`, and
  `_session_from_row` for the two new fields.
* `ConversationObservationItem` now exposes both fields.
* Added `record_advancement_attempt(*, tenant_id, conversation_id, outcome,
  parse_error_category=None) -> ConversationSession` to
  `ConversationStateStore`/`PostgresConversationStateStore`, outside
  `StorageInterface`.
* `record_advancement_attempt(...)` validates `outcome` against
  `ADVANCEMENT_OUTCOME_VALUES` and `parse_error_category` against
  `PARSE_ERROR_CATEGORY_VALUES = frozenset({"PARSER_ERROR"})`, uses a
  tenant-scoped `SELECT ... FOR UPDATE`, increments `version`, updates
  `updated_at`, and returns the updated `ConversationSession`.
* `ConversationAdvancementService.advance(...)` now records best-effort
  observability via the new `_record_outcome(...)` helper after the outcome
  is decided.
* `TURN_APPENDED_INCOMPLETE` records `latest_parse_error_category =
  "PARSER_ERROR"`. `PARSE_INCOMPLETE`, `DRAFT_CREATED`, and
  `ALREADY_HAS_DRAFT` are recorded with `latest_parse_error_category = None`,
  clearing any previously recorded category.
* `DUPLICATE_MESSAGE` is intentionally not recorded; `advance(...)` returns
  immediately with no call to `record_advancement_attempt(...)` and no
  session mutation.
* Implemented in `1b33d8a feat(m9): add conversation advancement
  observability storage` and `eb4c235 feat(m9): record conversation
  advancement observability`.

Safety conclusions:

* Observability recording is best-effort telemetry; it never changes the
  caller-visible `ConversationAdvancementResult`.
* If `record_advancement_attempt(...)` raises, `_record_outcome(...)` logs
  `logger.warning(..., exc_info=True)` and returns the original result
  unchanged; recording failures never raise out of `advance(...)`.
* `DUPLICATE_MESSAGE` intentionally does not record or mutate session
  observability.
* `ALREADY_HAS_DRAFT` is recorded for legitimate new post-draft/recovery
  paths (orphan-draft recovery, post-`draft_created` follow-up, and
  create-draft-conflict recovery).
* Raw parser/LLM error text is never persisted; only the safe `PARSER_ERROR`
  category is stored.
* `latest_parse_status` was intentionally not added; only
  `latest_advancement_outcome` and `latest_parse_error_category` exist.

Explicitly excluded:

* No UI.
* No outbound replies.
* No idle/session-expiry behavior.
* No draft amendment.
* No `web/inbound.py` cleanup.
* No parser prompt or `PROMPT_VERSION` changes.
* No `StorageInterface` changes.
* `live_sheets` was not run.

### M9.4E - Idle-boundary design and deferral

Status: closed (design/deferral only).

Scope completed:

* Added `docs/M9_4E_IDLE_BOUNDARY_DESIGN.md`, documenting the intended idle
  policy, the required cross-session invariant for
  `(tenant_id, customer_phone)`, why a runtime implementation attempt was
  deferred, and the future prerequisite.
* Documented the intended idle policy: idle boundary =
  `received_at - open_session.last_message_at > DEFAULT_IDLE_THRESHOLD`
  (default 4 hours), applies only to `status="open"`, `draft_created` never
  auto-expires, `expired`/`failed` remain terminal/non-routable, and a
  post-idle message starts a brand-new conversation with
  `sequence_number=1` and no inherited transcript context.
* Documented the required invariant: at most one routable session per
  `(tenant_id, customer_phone)`; a successful `create_draft(...)` must
  always drive its producing session to `draft_created`/
  `resulting_order_id`; idle expiry must never let a new `open` session win
  over a customer's existing `draft_created` session; `mark_draft_created`
  must never silently no-op into an expired/unlinked row.
* Added
  `tests/test_conversation_state_store.py::test_draft_created_session_remains_latest_over_later_open_session_for_customer`
  as a `strict=True` xfail acceptance test reproducing the invalid terminal
  state (`old=draft_created`, `new=open` and `latest`) that a future
  implementation must prevent.
* Confirmed runtime behavior is unchanged from `e84a844`:
  `conversation_state.py`, `conversation_observation.py`, and
  `conversation_advancement.py` are unchanged; no migration; no
  `status="expired"` is written by runtime code.

Why deferred:

* A prior implementation attempt proved that store-method-scoped
  `pg_advisory_xact_lock` cannot protect the
  `_route_session -> append_turn_if_new -> parse -> create_draft ->
  mark_draft_created -> record_advancement_attempt` lifecycle, because each
  `PostgresConversationStateStore` method opens its own
  `session_scope`/transaction and the lock releases between methods.
* Correct idle-boundary behavior requires a lifecycle-spanning, per-customer
  unit of work, which is a larger architectural change out of scope for
  M9.4E.

Deferred to a future milestone ("Lifecycle-spanning unit of work for
conversation advancement"):

* Runtime idle-boundary expiry
  (`get_or_create_open_session_after_idle_boundary(...)` or equivalent).
* Per-customer serialization across the full advancement lifecycle,
  including how to bound serialization around parser/LLM/network latency.
* Any change to `get_latest_session_for_customer(...)` ordering needed so a
  `draft_created` session wins "latest" over a later `open` session for the
  same customer, or an equivalent prevention of the competing `open`
  session.

Remaining M9.4 scope: none. M9.4 is closed.

## M9.5 - Operator conversation visibility

Status: M9.5A closed (read-only session list). M9.5B closed (read-only
session detail / ordered turns drill-down).

M9.5 adds operator-facing visibility into conversation sessions, building on
the M9.4C/M9.4D observation read model. M9.4E's idle-boundary deferral still
applies: runtime never writes `status="expired"`, so `status="open"` with
observed idle activity is a real, presentable state rather than a lifecycle
state.

### M9.5A - Operator conversation visibility (read-only session list)

Status: closed.

Scope completed:

* Added a read-only Streamlit page, `pages/6_Conversations.py`, listing
  recent conversation sessions for the active tenant via the existing
  tenant-scoped
  `PostgresConversationObservationReads.get_conversation_observation_snapshot(...)`
  read model. Pure presentation: no `StorageInterface` change, no new
  storage method, no migration.
* Postgres-only guarded, via `get_conversation_observation_reads(storage)`
  mirroring the `get_inbound_draft_review_service` pattern.
* Filters: status, customer phone/search, latest advancement outcome,
  latest parse-error category, and recent activity (time window).
* `status="open"` with `is_idle=True` is rendered as "Open - observed idle
  (not expired)", distinct from plain "Open", with explanatory copy that
  idle is an observed read-time signal and not a persisted expiry -
  consistent with M9.4E's deferral.
* Added an AST read-only guard (`READ_ONLY_RUNTIME_PAGES` and
  `test_read_only_runtime_pages_do_not_use_mutation_apis`) so the page
  cannot import or call mutation APIs.

Explicitly excluded:

* No session detail view and no ordered turn rendering / `list_turns`.
* No draft amendment, approve/reject changes, outbound WhatsApp replies,
  Twilio callbacks, queue/worker, or payment logic.
* No runtime idle-expiry behavior (remains deferred from M9.4E).

Delivered in M9.5B:

* Session detail view / ordered per-turn (transcript) rendering - see
  M9.5B below.

### M9.5B - Operator conversation session detail (read-only ordered turns)

Status: closed.

Scope completed:

* Added a tenant-scoped observation detail read,
  `PostgresConversationObservationReads.get_conversation_observation_detail(
  *, tenant_id, conversation_id, now, idle_threshold)`, plus
  `ConversationTurnObservationItem` and `ConversationObservationDetail`
  DTOs, sibling to `get_conversation_observation_snapshot`. Scoped by
  `tenant_id` AND `conversation_id`; a wrong-tenant or unknown
  `conversation_id` returns `None` and never exposes turns. No
  `StorageInterface` change, no migration.
* Extended `pages/6_Conversations.py` with a read-only "Session detail"
  view: a session selector, session metadata, and ordered turn previews
  (body preview only, capped via `LATEST_BODY_PREVIEW_LENGTH`). The page
  consumes `get_conversation_observation_detail` only - no `list_turns`,
  no raw query, no storage shortcut.
* `status="open"` with `is_idle=True` keeps the M9.5A distinct "Open -
  observed idle (not expired)" label, consistent with M9.4E's deferral.
* Missing `message_sid`/`from_number`, NULL session metadata, and
  zero-turn sessions all render gracefully; a `None` detail read (e.g.
  wrong tenant) renders a safe "session not found" message.
* Added an AST guard
  (`test_conversation_detail_pages_use_observation_detail_read_not_list_turns`)
  asserting the page calls `get_conversation_observation_detail` and never
  `list_turns`.

Explicitly excluded:

* No full customer message body rendering (preview only), no turn
  annotation/notes.
* No re-parse, re-run advancement, expire action, draft amendment,
  approve/reject, outbound, payment, queue/worker, or Twilio callbacks.
* No runtime idle-expiry behavior (remains deferred from M9.4E).

## M9.6 - Lifecycle-spanning per-customer unit of work for conversation advancement

Status: M9.6A closed (design only); M9.6B closed (validation spike only);
M9.6C closed (production store foundation, unwired). M9.6D (runtime wiring)
deferred.

M9.6 delivers the prerequisite identified in
`docs/M9_4E_IDLE_BOUNDARY_DESIGN.md` section 4: a lifecycle-spanning,
per-customer unit of work for `ConversationAdvancementService.advance(...)`,
needed before any mutating conversation feature (runtime idle expiry, draft
amendment, outbound replies, payment/confirmation) can be designed safely.

### M9.6A - Conversation advancement unit-of-work design

Status: closed (design only).

Scope completed:

* Added `docs/M9_6_CONVERSATION_UOW_DESIGN.md`, documenting current runtime
  facts (one `Session`/transaction per store method via `session_scope`, no
  shared-session seam, no unit-of-work abstraction, `advance(...)` spans many
  independent transactions, parser/LLM call already outside any transaction),
  the serialization key concept `conversation_customer_key(tenant_id,
  customer_phone)`, required invariants, and four strategies considered.
* Recommended strategy: a durable per-customer claim/lock row with lease
  semantics, short DB transactions for each lifecycle step, and a post-parse
  short critical section with revalidation as a defense-in-depth final step
  before committing draft/session outcomes.
* Documented a future `advance(...)` integration sequence (duplicate
  `MessageSid` gate first and outside the claim; claim acquisition before
  routing; parser outside any DB transaction but inside the logical claim;
  revalidation after parsing; commit; record outcome; release claim), a
  future `conversation_customer_claims`-style schema concept (no migration),
  how runtime idle expiry becomes the first consumer and closes the M9.4E
  xfail, retry/error/lease semantics, an M9.7 conformance checklist, and
  future acceptance tests.

Explicitly excluded:

* No runtime implementation, no migration, no `StorageInterface` change, no
  advisory-lock validation spike, no tests added.
* No draft amendment, outbound replies, payment flow, or parser prompt
  change.

### M9.6B - Customer-claim validation spike

Status: closed (validation spike only).

Scope completed:

* Added `tests/test_conversation_customer_claim_spike.py`, a
  `live_postgres`-only validation spike for the durable per-customer
  claim/lock row recommended in `docs/M9_6_CONVERSATION_UOW_DESIGN.md`
  sections 6/7.
* The spike creates and drops a test-only
  `conversation_customer_claims_spike` table directly via SQL in a
  module-scoped fixture; it is not Alembic-managed and is not part of
  `Base.metadata`.
* Test-local helpers `acquire_claim(...)` / `release_claim(...)`, each
  exactly one short `engine.begin()` transaction (an
  `INSERT ... ON CONFLICT (tenant_id, customer_key) DO UPDATE ... WHERE
  lease_expires_at <= :now RETURNING ...` upsert).
* Proved against real Postgres:
  * same `(tenant_id, customer_key)` serializes two concurrent workers -
    Worker B cannot acquire while Worker A's lease is live, and only
    succeeds after Worker A releases (order proven via `threading.Event`
    and a recorded event sequence, not sleeps alone);
  * different customers (different `customer_key`, same `tenant_id`) do
    not block each other;
  * an expired lease can be taken over by a new holder; a live
    (non-expired) lease cannot be taken over;
  * `acquire_claim`/`release_claim` hold no checked-out connection
    (`engine.pool.checkedout() == 0`) during a simulated parser delay.
* 4 new tests, all passing under `pytest -m live_postgres`.

Explicitly excluded:

* No migration; no `conversation_customer_claims` production table.
* No production ORM model or store class.
* No `StorageInterface` change.
* No wiring into `ConversationAdvancementService.advance(...)`.
* No webhook, draft amendment, idle expiry, outbound, payment, or parser
  changes.

### M9.6C - Production customer-claim store foundation

Status: closed (production store foundation, unwired).

Scope completed:

* Added migration `5eb2de4cca12` (`down_revision = 11605e30520d`) creating
  `conversation_customer_claims` (`tenant_id`, `customer_key`, `holder_id`,
  `acquired_at`, `lease_expires_at`, `updated_at`, composite primary key
  `(tenant_id, customer_key)`); added `ConversationCustomerClaimRow` to
  `postgres_models.py` and `CONVERSATION_CUSTOMER_CLAIMS_TAB` to
  `schema.py`. `ALEMBIC_HEAD_REVISION` in `tests/test_smoke_preflight.py`
  updated to `5eb2de4cca12`.
* Added `src/duna_orders/storage/conversation_customer_claims.py`: Protocol
  `ConversationCustomerClaimStore`, `PostgresConversationCustomerClaimStore`
  (narrow store outside `StorageInterface`, `session_factory`-constructed,
  same pattern as `ConversationOrderLookup`/`OutboundAcknowledgementStore`),
  pure helper `normalize_customer_claim_key(tenant_id, customer_phone)`
  (delegates to `normalize_customer_phone`, does not embed `tenant_id` in
  the result), and `DEFAULT_CLAIM_LEASE_DURATION = timedelta(seconds=60)`.
* `try_acquire`/`renew` use a single atomic SQL statement each
  (`INSERT ... ON CONFLICT (tenant_id, customer_key) DO UPDATE ... WHERE
  lease_expires_at <= now() RETURNING holder_id` and
  `UPDATE ... SET lease_expires_at = now() + :lease_duration, updated_at =
  now() WHERE ... AND holder_id = :holder_id RETURNING holder_id`,
  respectively), both using the DB clock (`now()`), not app time.
  `release` is `DELETE ... WHERE ... AND holder_id = :holder_id RETURNING
  holder_id`. No select-then-update.
* Added `tests/test_conversation_customer_claim_store.py`: pure
  `normalize_customer_claim_key` tests plus `live_postgres` tests against
  the real `conversation_customer_claims` table covering acquire-when-
  missing, acquire-blocked-by-live-lease, expired-lease takeover,
  release/renew holder-mismatch rejection, renew-extends-lease,
  same-customer serialization (`threading.Event`-coordinated) and
  different-customer independence, and no-held-connection during a
  simulated parser delay.
* Added an architecture-guard test
  (`test_no_runtime_module_imports_conversation_customer_claim_store` in
  `tests/test_architecture_boundaries.py`) asserting no module under
  `src/duna_orders/services/`, `src/duna_orders/web/`, `src/duna_orders/ui/`,
  or `pages/` imports the claim store module or its exported names.
* Documented the production store in `docs/M9_6_CONVERSATION_UOW_DESIGN.md`
  section 15.

Explicitly excluded:

* No `StorageInterface` change; no `storage/factory.py`,
  `web/app.py`, or `ui/setup.py` change.
* No wiring into `ConversationAdvancementService.advance(...)`.
* No webhook, UI, parser, idle expiry, draft amendment, outbound, or payment
  changes.

### M9.6D - Runtime wiring (deferred)

Status: not started.

M9.6D will wire `try_acquire`/`renew`/`release` into
`ConversationAdvancementService.advance(...)` per the sequence sketched in
`docs/M9_6_CONVERSATION_UOW_DESIGN.md` section 8, and is the point at which
`DEFAULT_CLAIM_LEASE_DURATION` should be revisited against real pilot
parse-latency data.

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

### M8.6.3E - Retry max-attempt enforcement

Closed.

Completed scope:

* Enforced a maximum of `2` total attempts per outbound acknowledgement row.
* Suppressed failed rows with `attempt_count >= 2` using
  `suppressed_retry_limit_reached`, even when `retry_failed=True`.
* Mapped max-attempt suppression to
  `Acknowledgement was not sent. Manual follow-up is required.`
* Kept failed rows with `attempt_count < 2` retryable in Orders Today.
* Hid retry for failed rows with `attempt_count >= 2`.
* Preserved backend claim/idempotency as final authority; stale UI cannot
  bypass the max-attempt rule.
* Preserved non-retryable `unknown`, `sending`, `send_requested`, and `sent`
  behavior.
* Manual UI smoke passed on the throwaway Neon branch using
  `ord_ui_retry_limit_attempt1_smoke_20260610` and
  `ord_ui_retry_limit_attempt2_smoke_20260610`.

Deferred follow-ups:

* Optional `attempt_count` display.
* Optional last failure time display.
* Delivery/read callbacks.
* Queue/worker behavior.
* Auto-send on confirm.
* Payment-dependent acknowledgement content.
* Privacy/UX review for full phone display in Orders Today cards.

### M8.6.3C - Guarded retry execution smoke

Closed.

Completed scope:

* Performed smoke-only validation of the M8.6.3B retry UI against the
  throwaway Neon branch.
* Used a safe operator-controlled WhatsApp recipient ending in `4241`.
* Verified the real retry execution path from Orders Today UI through service,
  store, and Twilio.
* Confirmed the retry reused the same outbound idempotency row:
  `out_ui_retry_execution_smoke_20260610`.
* Confirmed the outbound row count stayed `1`.
* Confirmed `attempt_count` increased from `1` to `2`.
* Confirmed the row reached `status=sent` with provider message id and sent
  timestamp populated.
* Confirmed the WhatsApp message was received by the safe test recipient.
* Made no code changes.

Smoke evidence:

* Source safe-recipient order: `demo_ord_01486`.
* Retry execution order: `ord_ui_retry_execution_smoke_20260610`.
* Masked recipient: `****4241`.
* Result: `RETRY_EXECUTION_SMOKE_RESULT=PASS`.

Deferred follow-ups:

* `attempt_count` display.
* Last failure time display.
* Delivery/read callbacks.
* Queue/worker behavior.
* Auto-send on confirm.
* Payment-dependent acknowledgement content.

### M8.6.3B - Retry acknowledgement UI implementation

Closed.

Completed scope:

* Added a guarded `Retry acknowledgement` UI in Orders Today for outbound
  acknowledgement rows with `status=failed`.
* Rendered failed rows as:
  `Acknowledgement was not sent. You can retry.`
* Required an explicit confirmation step before retry fires, using:
  `Send this acknowledgement again? The previous attempt failed.`
* Routed confirmed retry through
  `OutboundAcknowledgementService.send_order_confirmed_acknowledgement(..., retry_failed=True)`.
* Kept the UI from calling provider adapters or creating outbound rows.
* Preserved backend claim/idempotency as final send authority.
* Hid retry for `sent`, `sending`, `send_requested`, `unknown`, no-record,
  blocked/missing-detail, and disabled/not-ready states.
* Preserved existing `Send acknowledgement` behavior for no-record rows.
* Manual Streamlit UI-gate smoke passed using seeded failed-row order
  `ord_ui_retry_failed_smoke_20260610`.

Deferred follow-ups:

* `attempt_count` display.
* Last failure time display.
* Delivery/read callbacks.
* Queue/worker behavior.
* Auto-send on confirm.
* Payment-dependent acknowledgement content.

### M8.6.2A - New Order session-state initialization guard

Closed.

Completed scope:

* Fixed the New Order page missing-key crash for
  `st.session_state.catalog_ready`.
* Added a missing-key guard in `pages/1_New_Order.py`.
* Preserved existing `catalog_ready` values and initialized the key only when
  missing.
* Added regression coverage in `tests/test_new_order_session_state.py`.
* Manual Streamlit smoke passed with safe local settings:
  `DUNA_STORAGE_BACKEND=memory` and `DUNA_OUTBOUND_ENABLED=false`.

Explicitly not included:

* No parser behavior or `PROMPT_VERSION` changes.
* No outbound behavior changes.
* No Orders Today changes.
* No storage contract changes.
* No catalog, product, or order business-rule changes.

### M8.6.1D - Provider-neutral outbound unavailable UI messages

Closed.

Completed scope:

* Updated Orders Today acknowledgement unavailable/not-ready rendering so
  provider-specific setup diagnostics are not exposed in the operator-facing UI.
* Preserved the disabled message exactly:
  `Outbound acknowledgement is disabled.`
* Rendered enabled-but-not-ready setup as:
  `Outbound acknowledgement is not fully configured.`
* Kept provider-specific setup diagnostics internal.
* Preserved send behavior, adapter behavior, preflight behavior, parser
  behavior, `StorageInterface`, and `OrderService` boundaries.

Deferred follow-ups:

* Retry-limit/max-attempts policy.
* `attempt_count` display.
* Last failure time display.
* Delivery/read callbacks.
* Queue/worker behavior.
* Auto-send on confirm.
* Payment-dependent acknowledgement content.

### M8.6.1C - Read-only manual acknowledgement status visibility

Closed.

Completed scope:

* Added read-only outbound acknowledgement status visibility to Orders Today for
  confirmed orders.
* Rendered no-record, sent, sending/send_requested, unknown/may-have-sent,
  failed retryable, and blocked/missing-detail states with safe
  operator-facing text.
* Showed `Send acknowledgement` only for the no-record state.
* Hid the send button for sent, sending, unknown, failed, and blocked states.
* Kept status visibility display-only; backend claim-before-send remains the
  final send authority.
* Kept the send button routed through
  `OutboundAcknowledgementService.send_order_confirmed_acknowledgement(...)`.
* Preserved disabled/not-ready behavior.
* Manual Streamlit smoke passed for disabled, sent existing-row, and no-record
  states.

Smoke evidence:

* Sent-row smoke used order `ord_ui_dup_smoke_20260610` and outbound row
  `out_01ktr4e71rw6hqeadbyb5dwgq7`.
* No-record smoke used order `ord_ui_no_record_smoke_20260610` with
  `OUTBOUND_ACK_ROW_COUNT 0`.

Deferred follow-ups:

* Retry-limit/max-attempts policy.
* `attempt_count` display.
* Last failure time display.
* Delivery/read callbacks.
* Queue/worker behavior.
* Auto-send on confirm.
* Payment-dependent acknowledgement content.

### M8.6.1B - Manual acknowledgement UI

Closed.

Completed scope:

* Added a pure UI mapper for outbound acknowledgement service outcomes.
* Added UI setup/factory readiness for outbound acknowledgement service
  construction.
* Added the operator-triggered manual acknowledgement action to Orders Today.
* Rendered the acknowledgement section only for confirmed orders.
* Kept sends behind an explicit `Send acknowledgement` button click.
* Displayed unavailable setup states as safe operator-facing messages.
* Displayed service results through the UI-safe outcome mapper.
* Kept provider internals out of the UI.
* Local memory/outbound-disabled safety smoke passed: confirmed cards showed
  `Acknowledgement`, displayed `Outbound acknowledgement is disabled.`, and did
  not show `Send acknowledgement`.
* Postgres UI duplicate-suppression smoke passed on the throwaway Neon smoke
  branch using seeded today-visible duplicate order
  `ord_ui_dup_smoke_20260610`.
* The UI click displayed `Acknowledgement was already sent.`, kept the existing
  outbound row count at `1`, kept the same `outbound_message_id`, kept
  `status=sent`, and kept `attempt_count=1`.
* No new WhatsApp send happened.

Deferred follow-ups:

* Retry-limit/max-attempts policy.
* `attempt_count` display.
* Last failure time display.
* Delivery/read callbacks.
* Queue/worker behavior.
* Auto-send on confirm.
* Payment-dependent acknowledgement content.

### M8.6.1A - Outbound acknowledgement core

Closed.

Completed scope:

* Added deterministic Colombian-Spanish order-confirmed acknowledgement
  rendering.
* Added durable outbound acknowledgement persistence and idempotency with
  `tenant_id + order_id + acknowledgement_type` as the unique key.
* Added operator-triggered service orchestration behind a provider-adapter
  protocol and fake adapter tests.
* Kept sends limited to confirmed orders.
* Proved claim-before-send behavior and non-resendable `sending`/`unknown`
  states.
* Kept outbound persistence outside `StorageInterface` and decoupled from the
  confirmation transaction.
* Added the real Twilio outbound adapter behind the proven provider-neutral
  boundary.
* Added env-gated outbound pilot configuration and preflight checks.
* Manual real Twilio outbound smoke passed on a throwaway Neon branch with
  confirmed order `demo_ord_01486`.
* Duplicate suppression passed with no second row and no second send side
  effect.
* The adapter normalizes plain E.164 customer phone snapshots to
  `whatsapp:+...` when the configured sender is a WhatsApp channel address.

Deferred follow-ups:

* Delivery/read callbacks.
* Queue/worker behavior.
* Auto-send on confirm.
* Payment-dependent acknowledgement content.

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

