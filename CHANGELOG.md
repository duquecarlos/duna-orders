# Changelog
## Unreleased

### M9.6D - Runtime wiring (customer claim + webhook dedup reorder)

Closed. Wires the M9.6C production customer-claim store into the live
Twilio webhook advancement path; serializes existing
`ConversationAdvancementService.advance(...)` behavior per customer, with no
new product behavior.

#### Delivered

* `POST /webhooks/twilio/whatsapp` now acquires the per-customer claim
  before recording `MessageSid`. Twilio signature/request validation
  remains the first gate, unchanged, and runs before claim acquisition.
  After validation, the webhook derives `tenant_id`/`customer_phone`,
  generates `holder_id = str(uuid4())`, computes `customer_key =
  normalize_customer_claim_key(tenant_id, customer_phone)`, and calls
  `claim_store.try_acquire(tenant_id=..., customer_key=...,
  holder_id=...)`.
* **Fixes the claim-busy redelivery hazard**: when `try_acquire` returns
  `False`, the webhook returns `HTTP 503` immediately, before
  `processed_message_store.try_record_message(...)`, before `advance(...)`,
  and before any conversation/session/draft state mutation or
  `latest_advancement_outcome` write. Because the `MessageSid` was never
  recorded, Twilio's redelivery for the same message re-enters processing
  (and re-attempts claim acquisition) instead of being treated as an
  already-processed duplicate. Logged via `logger.info(...)`. No new
  `ConversationAdvancementOutcome` value was added; claim-busy is resolved
  entirely at the webhook level, before `try_record_message`/`advance(...)`.
* On a successful `try_acquire`, the webhook enters a webhook-level
  `try/finally` wrapping `try_record_message(...)` and (if new)
  `advance(...)`; `finally` calls `claim_store.release(tenant_id=...,
  customer_key=..., holder_id=...)` on every exit path.
* **Genuine duplicate flow**: `try_acquire` succeeds -> `try_record_message`
  returns `False` (already recorded) -> webhook returns `200` without
  calling `advance(...)` -> `finally` releases the claim. Each duplicate
  delivery now does one extra acquire/release round trip; accepted as the
  cost of claim-based serialization.
* Added `ConversationAdvancementService.advance(...,
  renew_customer_claim: Callable[[], bool] | None = None)`.
  `conversation_advancement.py` does not import the claim-store module - it
  receives only this opaque callback. `web/app.py` supplies
  `renew_customer_claim() -> bool: return claim_store.renew(tenant_id=...,
  customer_key=..., holder_id=...)`.
* In `_advance_open_session(...)`, `renew_customer_claim()` is called once,
  after the parser/LLM call returns and before any draft/session write (the
  parser/LLM call has no upper bound, so the lease may have expired during
  it). If it returns `False`, the lifecycle returns
  `ConversationAdvancementOutcome.TURN_APPENDED_INCOMPLETE` (the turn was
  already appended; `parse_error_category=None`, distinguishing claim loss
  from a genuine `PARSER_ERROR`) and aborts the write phase without creating
  a draft or marking the session `draft_created`. No new outcome enum value
  was added.
* Added `ConversationAdvancementResult.parse_error_category: str | None =
  None` to carry this distinction.
* Added post-parse revalidation: immediately before `create_draft(...)`,
  `_advance_open_session(...)` calls
  `_recover_from_create_draft_conflict(tenant_id=..., session=...)` a
  second time. If a draft was created for this conversation during parsing,
  this routes through the existing `ALREADY_HAS_DRAFT` recovery logic - the
  same path already used for the post-`create_draft` `IntegrityError`
  recovery - with no new logic or enum value.
* Added `_get_conversation_customer_claim_store(app)` to `web/app.py`,
  lazily constructing
  `PostgresConversationCustomerClaimStore(get_or_create_session_factory(database_url))`
  when not injected, mirroring `_get_processed_message_store(app)`. The
  production webhook continues to require Postgres-backed storage for
  advancement; there is no no-op/in-memory claim store in production.
* Updated `tests/test_architecture_boundaries.py`'s claim-store import
  guard from "forbidden everywhere under services/web/ui/pages" to an
  allowlist: `CLAIM_STORE_ALLOWED_IMPORT_MODULES = {Path(
  "src/duna_orders/web/app.py")}`. The import remains forbidden in
  `conversation_advancement.py` (Option B avoids it) and every other
  scanned module. Added
  `test_web_app_imports_conversation_customer_claim_store()` so an unused
  allowlist entry cannot silently pass.
* Added webhook tests (`tests/test_web_twilio_webhook.py`): claim-busy
  returns `503` without recording `MessageSid`, calling the parser, calling
  `advance(...)`, or creating any order
  (`test_twilio_webhook_claim_busy_returns_503_without_processing`);
  genuine-duplicate extra acquire/release round trip without advancing
  (`test_twilio_webhook_genuine_duplicate_does_extra_claim_round_trip_without_advancing`);
  each duplicate `MessageSid` request does its own claim round trip
  (`test_twilio_webhook_duplicate_message_sid_each_request_does_own_claim_round_trip`);
  the renew callback invokes `claim_store.renew(...)`
  (`test_twilio_webhook_advance_renew_callback_invokes_claim_store_renew`).
  Added a `FakeConversationCustomerClaimStore` test double and a
  `_create_app(...)` wrapper defaulting an isolated fake claim store for
  every `create_app(...)` call site. Added acquire/release-pairing and
  `held == {}` assertions to the success, existing-`MessageSid`,
  parser-failure, and all-five-outcome tests.
* Added conversation-advancement tests
  (`tests/test_conversation_advancement.py`): `renew_customer_claim` is
  called once, after the parser returns and before any draft/session write
  (`test_renew_customer_claim_called_after_parse_before_draft_write`); a
  `renew_customer_claim` returning `False` aborts the write phase with
  `TURN_APPENDED_INCOMPLETE`, no draft, no order, and
  `latest_parse_error_category=None`
  (`test_renew_customer_claim_failure_aborts_write_phase_without_draft`);
  post-parse revalidation detects a concurrent draft created during parsing
  and returns `ALREADY_HAS_DRAFT` against the existing order
  (`test_post_parse_revalidation_detects_concurrent_draft_and_returns_already_has_draft`).
* Added two `live_postgres` concurrency tests
  (`tests/test_conversation_advancement.py`):
  `test_live_postgres_claim_serializes_same_customer_advance_and_creates_one_draft`
  (webhook-style acquire/advance/release loop for the same customer via
  `ThreadPoolExecutor`; the outcome set is now deterministically
  `{DRAFT_CREATED, ALREADY_HAS_DRAFT}` with one resulting order, unlike the
  pre-existing unguarded race test which could only assert a subset) and
  `test_live_postgres_claim_does_not_serialize_different_customers` (two
  different customers' claims do not block each other; both reach
  `DRAFT_CREATED` with distinct orders).

#### Excluded

* No migration; no `StorageInterface` change.
* No new `ConversationAdvancementOutcome` enum value.
* No runtime idle-boundary expiry. The M9.4E `strict=True` xfail
  (`tests/test_conversation_state_store.py::test_draft_created_session_remains_latest_over_later_open_session_for_customer`)
  remains unchanged and xfailed; idle-expiry runtime is deferred to M9.6E.
* No draft amendment, outbound replies, payment flow, parser prompt, or
  `PROMPT_VERSION` changes.
* No UI changes.
* `live_sheets` was not run.

#### Deferred

* A live/manual Twilio redelivery smoke for the claim-busy `503` path
  (confirm Twilio retries on `503` and that the redelivered `MessageSid` is
  processed rather than swallowed as a duplicate) has not been performed and
  is needed before relying on this behavior in production.
* `DEFAULT_CLAIM_LEASE_DURATION = timedelta(seconds=60)` is unchanged and
  remains tunable pending real pilot parse-latency data, per
  `docs/M9_6_CONVERSATION_UOW_DESIGN.md` section 15.
* Idle-boundary expiry runtime (M9.6E).

#### Verification

* `pytest -q` -> `658 passed, 45 deselected, 1 xfailed`.
* `pytest tests/test_conversation_advancement.py -q -m live_postgres` ->
  `3 passed`.
* `pytest tests/test_conversation_customer_claim_store.py -q -m
  live_postgres` -> `9 passed` (regression, unaffected).
* `ruff check src tests pages` -> all checks passed.
* `python -m compileall src tests pages` -> passed.
* `git diff --check` -> passed (only benign LF/CRLF warnings).
* `alembic heads` -> `5eb2de4cca12 (head)`; unchanged, no migration added.

### M9.6C - Production customer-claim store foundation

Closed. Foundation only; unwired from runtime.

#### Delivered

* Added `CONVERSATION_CUSTOMER_CLAIMS_TAB = "conversation_customer_claims"` to
  `src/duna_orders/storage/schema.py` and a corresponding
  `ConversationCustomerClaimRow` ORM model to
  `src/duna_orders/storage/postgres_models.py` (`tenant_id`, `customer_key`
  composite primary key; `holder_id`, `acquired_at`, `lease_expires_at`,
  `updated_at`).
* Added migration `5eb2de4cca12_add_conversation_customer_claims.py`
  (`down_revision = 11605e30520d`, new Alembic head) creating
  `conversation_customer_claims` with composite primary key
  `(tenant_id, customer_key)`; `downgrade()` drops the table. Updated
  `ALEMBIC_HEAD_REVISION` in `tests/test_smoke_preflight.py` to
  `5eb2de4cca12`.
* Added `src/duna_orders/storage/conversation_customer_claims.py`: pure
  helper `normalize_customer_claim_key(tenant_id, customer_phone) -> str`
  (delegates to `normalize_customer_phone`; `tenant_id` validated but not
  embedded in the result - a future migration seam), module constant
  `DEFAULT_CLAIM_LEASE_DURATION = timedelta(seconds=60)`, Protocol
  `ConversationCustomerClaimStore`, and
  `PostgresConversationCustomerClaimStore(session_factory)` (narrow store
  outside `StorageInterface`, same construction pattern as
  `ConversationOrderLookup`/`OutboundAcknowledgementStore`).
* `try_acquire`/`release`/`renew` are each a single atomic SQL statement
  executed via `session_scope(...)`, using the database clock (`now()`),
  not app time: `try_acquire` is
  `INSERT ... ON CONFLICT (tenant_id, customer_key) DO UPDATE ... WHERE
  conversation_customer_claims.lease_expires_at <= now() RETURNING
  holder_id`; `release` is `DELETE ... WHERE tenant_id = :tenant_id AND
  customer_key = :customer_key AND holder_id = :holder_id RETURNING
  holder_id`; `renew` is `UPDATE ... SET lease_expires_at = now() +
  :lease_duration, updated_at = now() WHERE ... AND holder_id =
  :holder_id RETURNING holder_id`. No select-then-update.
* Added `tests/test_conversation_customer_claim_store.py`: 4 pure tests for
  `normalize_customer_claim_key` (deterministic, equivalent phone formats
  collapse to the same key, different phones differ, `tenant_id` not
  embedded) and 9 `live_postgres` tests against the real
  `conversation_customer_claims` table covering: acquire when no claim
  exists, acquire blocked by a live lease, expired-lease takeover (and the
  stale holder's subsequent `renew` returning `False`), `release` succeeding
  only for the matching holder and returning `False` on mismatch, `renew`
  extending the lease for the matching holder and returning `False` on
  mismatch, same-customer concurrency serializing deterministically via
  `threading.Event`, different customers not blocking each other, and no
  held DB connection across a simulated parser delay.
* Added `test_no_runtime_module_imports_conversation_customer_claim_store` to
  `tests/test_architecture_boundaries.py`: an AST-walk guard over
  `src/duna_orders/services/`, `src/duna_orders/web/`,
  `src/duna_orders/ui/`, and `pages/` that fails if any of those modules
  imports `duna_orders.storage.conversation_customer_claims` or its
  `PostgresConversationCustomerClaimStore` / `ConversationCustomerClaimStore`
  / `normalize_customer_claim_key` symbols.
* Updated `tests/test_postgres_models.py`'s `POSTGRES_ONLY_TABLES` to include
  `conversation_customer_claims` (Alembic-managed, not part of the
  Google-Sheets-era `TABS`).
* Documented the production store foundation in
  `docs/M9_6_CONVERSATION_UOW_DESIGN.md` section 15.

#### Excluded

* No wiring into `ConversationAdvancementService.advance(...)`.
* No webhook, UI, parser, idle expiry, draft amendment, outbound, or payment
  changes.
* No `StorageInterface` change; no `storage/factory.py`, `web/app.py`, or
  `ui/setup.py` change. The store is constructed directly via
  `PostgresConversationCustomerClaimStore(session_factory)`.
* `conversation_customer_claims` is not imported by any runtime module
  (enforced by the new architecture guard) - only its own tests use it.

#### Verification

* `pytest tests/test_conversation_customer_claim_store.py -q -m
  live_postgres` -> `9 passed, 4 deselected`.
* `pytest -q` -> `650 passed, 43 deselected, 1 xfailed`.
* `ruff check src tests pages` -> all checks passed.
* `python -m compileall src tests pages` -> passed.
* `git diff --check` -> passed.
* `alembic heads` -> `5eb2de4cca12 (head)`.
* Migration round trip: `alembic upgrade head` -> `alembic downgrade -1`
  (drops `conversation_customer_claims`) -> `alembic upgrade head`
  (recreates it) - clean, no errors.

### M9.6B - Customer-claim validation spike

Validated. Spike only; no runtime/migration/StorageInterface changes.

#### Delivered

* Added `tests/test_conversation_customer_claim_spike.py`, a
  `live_postgres`-only validation spike for the durable per-customer
  claim/lock row recommended in `docs/M9_6_CONVERSATION_UOW_DESIGN.md`
  sections 6/7.
* A module-scoped fixture creates and drops a test-only
  `conversation_customer_claims_spike` table directly via SQL
  (`tenant_id`, `customer_key`, `holder_id`, `acquired_at`,
  `lease_expires_at`, `updated_at`, `PRIMARY KEY (tenant_id,
  customer_key)`); it is not Alembic-managed and is not part of
  `Base.metadata`.
* Test-local helpers `acquire_claim(...)` / `release_claim(...)` /
  `_read_claim(...)`, each performing exactly one short `engine.begin()`
  transaction. `acquire_claim(...)` is an
  `INSERT ... ON CONFLICT (tenant_id, customer_key) DO UPDATE ... WHERE
  conversation_customer_claims_spike.lease_expires_at <= :now RETURNING
  holder_id` - it inserts if no row exists, overwrites if the existing
  lease has expired, and matches no rows (acquire fails) if the existing
  lease is still live.
* Added 4 tests, all passing under `pytest -m live_postgres`:
  * `test_same_customer_claim_serializes_concurrent_workers` - two threads
    contend for the same `(tenant_id, customer_key)`; Worker B's
    `acquire_claim(...)` returns `False` while Worker A's lease is live and
    only succeeds after Worker A releases. Ordering is proven via a
    recorded event sequence (`a_acquired` -> `b_blocked` -> `a_released` ->
    `b_acquired`) coordinated with `threading.Event`, not sleeps alone.
  * `test_different_customers_do_not_block_each_other` - Worker A holds a
    claim for customer A indefinitely; Worker B acquires a claim for
    customer B (same `tenant_id`) immediately, without waiting.
  * `test_expired_lease_can_be_taken_over_but_live_lease_cannot` - a claim
    seeded with an already-expired `lease_expires_at` (simulated crashed
    holder) is taken over by a new holder; a subsequent attempt against the
    new holder's live lease returns `False` and leaves the row unchanged.
  * `test_acquire_and_release_hold_no_connection_during_simulated_parser_delay`
    - after `acquire_claim(...)` commits and returns,
    `engine.pool.checkedout() == 0` holds across a simulated parser/LLM
    delay (`time.sleep`, no DB call), confirming the claim survives as
    committed row state with no held connection/transaction.

#### Excluded

* No migration; no `conversation_customer_claims` production table.
* No production ORM model or store class.
* No `StorageInterface` change.
* No wiring into `ConversationAdvancementService.advance(...)`.
* No webhook, draft amendment, idle expiry, outbound, payment, or parser
  changes.

#### Verification

* `pytest tests/test_conversation_customer_claim_spike.py -q -m
  live_postgres` -> `4 passed`.
* `pytest -q` -> `645 passed, 34 deselected, 1 xfailed`.
* `ruff check src tests pages` -> all checks passed.
* `python -m compileall src tests pages` -> passed.
* `git diff --check` -> passed.
* `alembic heads` -> `11605e30520d (head)`; no migration added.

### M9.6A - Conversation advancement unit-of-work design

Documented. Design only; no runtime/migration/StorageInterface changes.

#### Delivered

* Added `docs/M9_6_CONVERSATION_UOW_DESIGN.md`, the design for a
  lifecycle-spanning, per-customer unit of work for
  `ConversationAdvancementService.advance(...)`, addressing the future
  prerequisite identified in `docs/M9_4E_IDLE_BOUNDARY_DESIGN.md` section 4.
* Documented the current runtime facts confirmed by the M9.6 pre-flight:
  `session_scope(...)` opens/commits/closes one `Session` per store method
  call, no shared-session seam or unit-of-work abstraction exists,
  `advance(...)` spans many independent transactions, and the parser/LLM
  call already sits outside any DB transaction.
* Defined the primary serialization key,
  `conversation_customer_key(tenant_id, customer_phone)` (concept only, no
  helper added), and documented why `conversation_id` is the wrong key (it
  is the contested resource, not the lock), why `tenant_id + customer_id`
  is the future cleaner key, and the phone-normalization risk between
  `conversation_sessions.customer_phone` (raw `from_number`) and
  `OrderService.create_draft`'s `normalize_customer_phone(...)`.
* Compared four strategies (shared session/session-scoped advisory lock,
  transaction-scoped advisory lock per store method, post-parse
  revalidation, durable per-customer claim/lock row with lease semantics)
  and recommended the durable claim-row strategy with post-parse
  revalidation as a defense-in-depth final step.
* Documented a future `advance(...)` integration sequence, a future
  `conversation_customer_claims`-style schema concept (no migration), how
  runtime idle expiry becomes the first consumer and closes the M9.4E
  xfail, retry/error/lease semantics, a conformance checklist for M9.7,
  and future acceptance tests.

#### Excluded

* No runtime implementation, no advisory-lock validation spike.
* No migration; no `conversation_customer_claims`/`conversation_customer_locks`
  table added.
* No `StorageInterface` change.
* No draft amendment, outbound replies, payment flow, or parser prompt
  change.
* No tests added.
* `live_sheets` was not run.

#### Verification

* `git diff --stat` -> `CHANGELOG.md`, `ROADMAP.md`, and
  `docs/M9_6_CONVERSATION_UOW_DESIGN.md` only.
* `git diff --check` -> passed (only benign LF/CRLF warnings).
* `python -m compileall src tests pages` -> passed.
* `pytest tests/test_conversation_state_store.py -q` -> 29 passed,
  4 deselected, 1 xfailed.
* `alembic heads` -> `11605e30520d (head)`; no migration added.

### M9.5B - Operator conversation session detail (read-only ordered turns)

Implemented.

#### Delivered

* Added a tenant-scoped observation detail read,
  `PostgresConversationObservationReads.get_conversation_observation_detail(
  *, tenant_id, conversation_id, now, idle_threshold=DEFAULT_IDLE_THRESHOLD)
  -> ConversationObservationDetail | None`, sibling to
  `get_conversation_observation_snapshot`. Scoped by `tenant_id` AND
  `conversation_id` in the query; returns `None` for an unknown
  `conversation_id` or a wrong-tenant lookup, and never exposes turns in
  that case.
* Added `ConversationTurnObservationItem` (per-turn preview: `turn_id`,
  `sequence_number`, `received_at`, `from_number`, `message_sid`,
  `body_preview`) and `ConversationObservationDetail` (`session` plus
  ordered `turns`) DTOs in
  `src/duna_orders/storage/conversation_observation.py`. No
  `StorageInterface` change, no new storage method, no migration.
* Extended `pages/6_Conversations.py` with a "Session detail" section: a
  session selector over the filtered session list, session metadata
  (`conversation_id`, `customer_phone`, status, `last_message_at`,
  `version`, `turn_count`, `linked_order_id`, `has_draft`, `is_idle`,
  `latest_advancement_outcome`, `latest_parse_error_category`,
  `needs_operator_attention`), and ordered turn previews (sequence number,
  received-at, from-number, message SID, body preview - capped via the
  existing `LATEST_BODY_PREVIEW_LENGTH` convention, never the full body).
* The page consumes `get_conversation_observation_detail` only - no direct
  `list_turns` call, no raw query, no storage shortcut.
* `status="open"` with `is_idle=True` renders with the same distinct "Open
  - observed idle (not expired)" label as M9.5A, not plain "Open" and not
  a persisted "expired" state.
* Missing `message_sid`/`from_number`, NULL session metadata, and
  zero-turn sessions all render as "Not set" / an informational message
  without error.
* If the detail read returns `None` (e.g. wrong-tenant or a
  since-removed session), the page shows a safe "Session not found for
  this tenant" message.

#### Guards

* Added `ConversationObservationDetail` /
  `get_conversation_observation_detail` to the `ConversationObservationReads`
  Protocol.
* Added `REQUIRED_OBSERVATION_DETAIL_READ_PAGES` and
  `test_conversation_detail_pages_use_observation_detail_read_not_list_turns`
  in `tests/test_architecture_boundaries.py`, an AST guard asserting the
  page calls `get_conversation_observation_detail` and never `list_turns`.
* `pages/6_Conversations.py` remains in `ENFORCED_RUNTIME_READ_MODULES` and
  `READ_ONLY_RUNTIME_PAGES`; `test_read_only_runtime_pages_do_not_use_mutation_apis`
  continues to cover the page (no mutation / re-parse / expire / amend /
  approve-reject imports or calls).
* Direct unit tests for the detail read, including the headline
  cross-tenant acceptance test (Tenant B requesting Tenant A's
  `conversation_id` returns `None` and never exposes Tenant A's turns),
  ordered turns, zero/single-turn sessions, and NULL metadata fields.

#### Excluded

* No full customer message body rendering (preview only).
* No turn annotation/notes, no re-parse, no re-run advancement, no expire
  action, no draft amendment, no approve/reject, no outbound, no payment,
  no queue/worker, no Twilio callbacks, no `live_sheets`.
* No runtime idle-expiry behavior change (remains deferred from M9.4E).

#### Verification

* `pytest -q` -> 645 passed, 30 deselected, 1 xfailed.
* `ruff check src tests pages` -> all checks passed.
* `python -m compileall src tests pages` -> passed.
* `alembic heads` -> `11605e30520d (head)`; no migration added; no
  `StorageInterface` change.
* `git diff --check` -> passed (only benign LF/CRLF warnings).

### M9.5A - Operator conversation visibility (read-only session list)

Implemented.

#### Delivered

* Added a read-only Streamlit operator page, `pages/6_Conversations.py`,
  listing recent conversation sessions for the active tenant.
* The page is pure presentation: it uses the existing tenant-scoped
  `PostgresConversationObservationReads.get_conversation_observation_snapshot(...)`
  read model. No `StorageInterface` change, no new storage method, and no
  migration.
* Added `get_conversation_observation_reads(storage)` to
  `src/duna_orders/ui/setup.py`, returning
  `PostgresConversationObservationReads | None` and mirroring the
  Postgres-only pattern used by `get_inbound_draft_review_service`.
* Added `src/duna_orders/ui/conversations.py`: NULL-safe row rendering,
  status labeling, and filters for status, customer phone, latest
  advancement outcome, latest parse-error category, and recent activity
  (time window).
* A session with `status="open"` and `is_idle=True` renders with a distinct
  "Open - observed idle (not expired)" label plus explanatory copy that
  idle is an observed, read-time signal, not a persisted expiry - consistent
  with M9.4E (runtime idle expiry remains deferred and runtime never writes
  `status="expired"`).

#### Guards

* Added `pages/6_Conversations.py` to `ENFORCED_RUNTIME_READ_MODULES` in
  `tests/test_architecture_boundaries.py`.
* Added `READ_ONLY_RUNTIME_PAGES` and
  `test_read_only_runtime_pages_do_not_use_mutation_apis`, an AST guard
  ensuring the page does not import or call mutation APIs
  (`mark_draft_created`, `record_advancement_attempt`,
  `review_inbound_draft`, `create_draft`, `append_turn_if_new`,
  `get_or_create_open_session`, order-status mutators, `list_turns`, etc.).

#### Excluded

* No session detail view and no per-turn rendering / `list_turns`.
* No draft amendment, approve/reject changes, outbound WhatsApp replies,
  Twilio callbacks, queue/worker, or payment logic.
* No runtime idle-expiry behavior change (remains deferred from M9.4E).

#### Verification

* `pytest -q` -> 623 passed, 30 deselected, 1 xfailed.
* `ruff check src tests pages` -> all checks passed.
* `python -m compileall src tests pages` -> passed.
* `alembic heads` -> `11605e30520d (head)`; `git diff -- alembic/versions`
  -> empty; no migration added.
* `git diff --check` -> passed.

### M9.4E - Idle-boundary design and deferral

Documented. Runtime idle-boundary expiry is deferred.

#### Delivered

* Added `docs/M9_4E_IDLE_BOUNDARY_DESIGN.md`, recording the intended idle
  policy, the required `(tenant_id, customer_phone)` invariant, why a
  runtime implementation attempt was deferred, and the future
  lifecycle-spanning unit-of-work prerequisite.
* Added
  `tests/test_conversation_state_store.py::test_draft_created_session_remains_latest_over_later_open_session_for_customer`,
  a `strict=True` xfail acceptance test reproducing the invalid terminal
  state (`old=draft_created`, `new=open` and `latest`) that a future
  implementation must prevent.
* Updated `ROADMAP.md`, `docs/M9_CONVERSATION_STATE_ARCHITECTURE.md`, and
  `docs/M9_4B_CONVERSATION_OBSERVABILITY_READ_MODEL_DESIGN.md` to close M9.4
  as design/deferral and cross-reference
  `docs/M9_4E_IDLE_BOUNDARY_DESIGN.md`.

#### Reverted

* A prior uncommitted M9.4E runtime attempt (advisory-lock-based
  `mark_draft_created(...)`, `get_or_create_open_session_after_idle_boundary(...)`,
  `_try_idle_boundary_transition(...)`, and `_route_session(...)`
  idle-boundary wiring) was fully reverted from
  `src/duna_orders/storage/conversation_state.py`,
  `src/duna_orders/storage/conversation_observation.py`, and
  `src/duna_orders/services/conversation_advancement.py` back to
  `e84a844`.
* `mark_draft_created(...)` is back to its exact pre-M9.4E behavior: it
  marks `status="draft_created"` / `resulting_order_id=order_id` regardless
  of the row's current status, is idempotent for the same `order_id`, and
  raises `ValueError` if linked to a different order.
* `DEFAULT_IDLE_THRESHOLD` remains defined locally in
  `conversation_observation.py`, as it was before the attempt.
* The broad M9.4E runtime test additions to
  `tests/test_conversation_state_store.py`,
  `tests/test_conversation_advancement.py`, and
  `tests/test_conversation_observation.py` were reverted, leaving only the
  one xfail acceptance test added on top of the `e84a844` baseline.

#### Excluded

* No runtime idle-boundary expiry behavior.
* No `status="expired"` writes by runtime code.
* No migration.
* No `StorageInterface` changes.
* No UI / operator page.
* `live_sheets` was not run.

#### Verification

* `alembic heads` -> `11605e30520d (head)`.
* `git diff -- alembic/versions` -> empty.
* `pytest tests/test_conversation_state_store.py -q` -> 29 passed,
  4 deselected, 1 xfailed.
* `pytest tests/test_conversation_advancement.py -q` -> 18 passed,
  1 deselected.
* `pytest tests/test_conversation_observation.py -q` -> 18 passed.
* `pytest tests/test_web_twilio_webhook.py -q` -> 25 passed.
* `pytest tests/test_postgres_models.py tests/test_smoke_preflight.py -q`
  -> 31 passed.
* `pytest tests/test_architecture_boundaries.py -q` -> 2 passed.
* `pytest -q` -> 602 passed, 30 deselected, 1 xfailed.
* `ruff check src tests pages` -> all checks passed.
* `python -m compileall src tests pages` -> passed.
* `git diff --check` -> passed.

### M9.4D - Persisted conversation advancement observability

Implemented in `1b33d8a feat(m9): add conversation advancement observability
storage` and `eb4c235 feat(m9): record conversation advancement
observability`.

#### Delivered

* Added migration `11605e30520d`, adding nullable `conversation_sessions`
  columns `latest_advancement_outcome` and `latest_parse_error_category`.
* Updated `ConversationSessionRow`, `ConversationSession`, and
  `_session_from_row` for the two new fields.
* `ConversationObservationItem` (and `_item_from_row`) now expose both
  fields.
* Added `record_advancement_attempt(*, tenant_id, conversation_id, outcome,
  parse_error_category=None) -> ConversationSession` to
  `ConversationStateStore` and `PostgresConversationStateStore`, outside
  `StorageInterface`.
* `record_advancement_attempt(...)` validates `outcome` against
  `ADVANCEMENT_OUTCOME_VALUES` (the five `ConversationAdvancementOutcome`
  values, defined independently in `conversation_state.py`) and
  `parse_error_category` against `PARSE_ERROR_CATEGORY_VALUES =
  frozenset({"PARSER_ERROR"})`, raising `ValueError` for an unknown
  outcome/category or an unknown/wrong-tenant `conversation_id`.
* `record_advancement_attempt(...)` re-selects the session row with a
  tenant-scoped `SELECT ... FOR UPDATE`, sets both fields, increments
  `version`, sets `updated_at = utc_now()`, flushes, and returns the updated
  `ConversationSession`.
* `ConversationAdvancementService.advance(...)` was restructured to a single
  return boundary: every branch assigns to a local `result`; if
  `result.outcome == DUPLICATE_MESSAGE`, `advance(...)` returns immediately
  without recording; otherwise it computes `parse_error_category =
  "PARSER_ERROR" if result.outcome == TURN_APPENDED_INCOMPLETE else None` and
  calls the new `_record_outcome(...)` exactly once.
* `_record_outcome(...)` wraps `record_advancement_attempt(...)` in
  try/except, logs `logger.warning(..., exc_info=True)` on any exception, and
  always returns `result` unchanged.
* Outcome -> recorded category mapping: `TURN_APPENDED_INCOMPLETE` ->
  `latest_parse_error_category = "PARSER_ERROR"`; `PARSE_INCOMPLETE`,
  `DRAFT_CREATED`, `ALREADY_HAS_DRAFT` -> `latest_parse_error_category =
  None` (clearing any previously recorded category); `DUPLICATE_MESSAGE` ->
  not recorded at all, with no `version`/`updated_at` mutation.
* Added 7 tests to `tests/test_conversation_state_store.py` for
  `record_advancement_attempt(...)`.
* Added `test_snapshot_exposes_latest_advancement_outcome_and_parse_error_category`
  to `tests/test_conversation_observation.py`.
* Updated `test_conversation_sessions_table_is_postgres_only` in
  `tests/test_postgres_models.py` for the two new columns.
* Added `_SpyConversationStateStore` and 9 new tests to
  `tests/test_conversation_advancement.py`, covering: each outcome records
  the expected `latest_advancement_outcome`/`latest_parse_error_category`;
  `DUPLICATE_MESSAGE` records nothing; and a `record_advancement_attempt(...)`
  failure logs a warning and leaves the returned
  `ConversationAdvancementResult` unchanged.
* Updated `ALEMBIC_HEAD_REVISION = "11605e30520d"` in
  `tests/test_smoke_preflight.py`.

#### Safety conclusions

* Observability recording is best-effort telemetry: it runs after the
  advancement outcome is already decided and never changes the
  caller-visible `ConversationAdvancementResult`.
* If `record_advancement_attempt(...)` raises for any reason,
  `_record_outcome(...)` logs a warning with `exc_info=True` and returns the
  original result unchanged; `advance(...)` does not raise and does not
  change its outcome because of a recording failure.
* `DUPLICATE_MESSAGE` intentionally does not call
  `record_advancement_attempt(...)` and does not mutate session
  observability, `version`, or `updated_at`, preserving the "no session
  mutation" guarantee from
  `docs/M9_2A_CONVERSATION_ADVANCEMENT_SERVICE_DESIGN.md`.
* `ALREADY_HAS_DRAFT` is recorded for legitimate new post-draft/recovery
  paths (orphan-draft recovery, post-`draft_created` follow-up, and
  create-draft-conflict recovery), all reached through the single return
  boundary in `advance(...)`.
* Raw parser/LLM error text is never persisted; only the safe
  `PARSER_ERROR` category from `PARSE_ERROR_CATEGORY_VALUES` is stored.
* `latest_parse_status` was intentionally not added; only
  `latest_advancement_outcome` and `latest_parse_error_category` exist on
  `conversation_sessions`.

#### Excluded

* No UI.
* No outbound replies.
* No idle/session-expiry behavior.
* No draft amendment.
* No `web/inbound.py` cleanup.
* No parser prompt or `PROMPT_VERSION` changes.
* No `StorageInterface` changes.
* `live_sheets` was not run.

#### Verification

* `alembic heads` -> `11605e30520d (head)`.
* `alembic upgrade head` succeeded.
* `pytest -q` -> `602 passed, 30 deselected`.
* `ruff check src tests pages` -> all checks passed.
* `python -m compileall src tests pages` -> passed.
* `git diff --check` -> passed.
* `git status --short` -> clean.

### M9.4C - Conversation observation read-model

Implemented in `bc2de4a feat(m9): add conversation observation read model`.

#### Delivered

* Added `src/duna_orders/storage/conversation_observation.py` with the
  `ConversationObservationReads` protocol and
  `PostgresConversationObservationReads`, outside `StorageInterface`.
* Added `ConversationObservationItem` / `ConversationObservationDiagnostics`
  / `ConversationObservationSnapshot` frozen dataclasses, mirroring
  `InboundDraftReviewItem` / `InboundReviewDiagnostics` /
  `InboundReviewSnapshot`.
* Added `get_conversation_observation_snapshot(*, tenant_id, now,
  idle_threshold=DEFAULT_IDLE_THRESHOLD)`, returning a tenant-scoped snapshot
  built from `conversation_sessions` and `conversation_turns` using three
  portable `select(...)` queries via `session_scope(...)` (no N+1, no
  Postgres-only `DISTINCT ON`).
* Computed read-time fields per item: `turn_count`, `latest_message_sid`,
  `latest_body_preview` (truncated to `LATEST_BODY_PREVIEW_LENGTH = 160`,
  preserving an empty `""` body separately from "no turns" `None`),
  `linked_order_id` (from `resulting_order_id`), `has_draft`, `is_idle`
  (`now - last_message_at > idle_threshold`, default
  `DEFAULT_IDLE_THRESHOLD = timedelta(hours=4)`), and
  `needs_operator_attention` (`status == "open" and linked_order_id is None
  and (turn_count >= ATTENTION_TURN_THRESHOLD or is_idle)`, with
  `ATTENTION_TURN_THRESHOLD = 3`).
* Added diagnostics counts: `total_count`, `open_count`,
  `draft_created_count`, `idle_count`, `needs_attention_count`.
* Sessions with zero turns are included with `turn_count=0`,
  `latest_message_sid=None`, `latest_body_preview=None`.
* Added `tests/test_conversation_observation.py` (17 tests, local
  SQLite-backed only).

#### Excluded

* No schema/migration changes.
* No changes to `ConversationStateStore`, `ConversationAdvancementService`,
  or `web/app.py`.
* No UI / operator page.
* No `latest_advancement_outcome`, `latest_parse_error_category`, or
  `latest_parse_status` (deferred to M9.4D).
* No idle/session-expiry behavior; `is_idle` is a read-time-only comparison,
  not a session-boundary policy.
* No `StorageInterface` changes.
* `PostgresConversationObservationReads` is not added to
  `ENFORCED_RUNTIME_READ_MODULES` or `KNOWN_STAGE1_RUNTIME_READ_MODULES`.
* `live_sheets` was not run.

#### Verification

* `pytest tests/test_conversation_observation.py -q` -> 17 passed.
* `pytest tests/test_conversation_state_store.py
  tests/test_conversation_advancement.py -q` -> 31 passed, 5 deselected.
* `pytest tests/test_architecture_boundaries.py -q` -> 2 passed.
* `pytest -q` -> 585 passed, 30 deselected.
* `ruff check src tests pages` -> all checks passed.
* `python -m compileall src tests pages` -> passed.
* `git diff --check` -> passed.

### M9.4B - Conversation observability/read-model design

Documented.

#### Delivered

* Added `docs/M9_4B_CONVERSATION_OBSERVABILITY_READ_MODEL_DESIGN.md`.
* Documented existing conversation observability (session and turn fields,
  `ConversationOrderLookup`) available today with no schema change.
* Split remaining M9.4 observability scope into M9.4C (read-only
  `ConversationObservationReads`/`PostgresConversationObservationReads`
  snapshot read-model, no schema change, outside `StorageInterface`) and
  M9.4D (persisted `latest_advancement_outcome` /
  `latest_parse_error_category` hooks via `record_advancement_attempt(...)`,
  requires migration and a safe-category policy for parse errors).
* Confirmed `opened_at`/`last_message_at` already support read-time idle
  visibility; idle-boundary behavior/policy remains a separate deferred
  slice.

#### Verification

* Documentation-only change.
* No code, tests, or migrations.

### M9.4A - Conversation advancement hardening tests

Implemented in `b5f38fe test(m9): harden conversation advancement wiring`.

#### Delivered

* Added `test_twilio_webhook_invalid_signature_does_not_record_processed_message`
  to `tests/test_web_twilio_webhook.py`, proving an invalid
  `X-Twilio-Signature` returns `403` without recording a `processed_messages`
  row or calling `ConversationAdvancementService.advance(...)`.
* Extended `test_twilio_webhook_rejects_missing_from_field` to assert a
  missing `From` does not record a `processed_messages` row for the rejected
  `MessageSid`.
* Extended
  `test_twilio_webhook_followup_message_after_draft_created_links_existing_order`
  to snapshot the existing draft order before and after a follow-up message
  and assert no mutation, and to replay the follow-up `MessageSid` (not the
  draft-creating `MessageSid`) and assert it remains idempotent with no
  reprocessing.
* Added
  `test_twilio_webhook_tenant_isolation_same_customer_creates_separate_conversations_and_drafts`,
  proving the same customer phone number across two `webhook_tenant_id`
  values produces separate conversations and separate draft orders through
  the webhook path.
* Added `test_live_postgres_concurrent_advance_for_same_customer_creates_one_draft`
  (`@pytest.mark.live_postgres`) to `tests/test_conversation_advancement.py`,
  proving concurrent `advance(...)` calls for the same tenant/customer
  converge to a single `resulting_order_id`.
* Added `src/duna_orders/web/app.py` to
  `tests/test_architecture_boundaries.py`'s `ENFORCED_RUNTIME_READ_MODULES`,
  so the webhook module is covered by the broad-read AST guard.

#### Safety conclusions

* Tests-only slice; no production code changed.
* No parser prompt or `PROMPT_VERSION` change.
* No `StorageInterface` or schema/migration changes.
* No outbound replies, UI, auto-confirmation, payment gate, or media handling.

#### Deferred

* Observability/read-model hooks for a later operator conversation view.
* Session idle-boundary behavior/design.
* `create_draft_from_inbound_message(...)` and `web/inbound.py` cleanup
  (carried over from M9.3A).

#### Verification

* `pytest tests/test_web_twilio_webhook.py -q` passed.
* `pytest tests/test_conversation_advancement.py
  tests/test_conversation_state_store.py
  tests/test_architecture_boundaries.py -q` passed.
* `git diff --check` passed with LF-to-CRLF warnings only.

### M9.3A - Webhook wiring to conversation advancement

Implemented in `1cf5b6a feat(m9): wire webhook to conversation advancement`.

#### Delivered

* `POST /webhooks/twilio/whatsapp` now calls
  `ConversationAdvancementService.advance(tenant_id=..., message_sid=...,
  from_number=..., body=..., received_at=utc_now())` instead of calling
  `create_draft_from_inbound_message(...)` directly.
* Signature validation remains the first gate and runs before any side
  effects; an invalid `X-Twilio-Signature` returns `403` without recording a
  processed message or calling the advancement service.
* `processed_messages.try_record_message(...)` (keyed by `MessageSid`) remains
  the first business/persistence gate. A duplicate `MessageSid` returns `200`
  without calling the advancement service or the parser.
* A new `MessageSid` calls `advance(...)` exactly once, after the idempotency
  pass succeeds.
* Added required-field validation for `From`: an empty/missing `From` now
  returns `400`, mirroring the existing `MessageSid` empty-field check, before
  `try_record_message(...)` runs. The raw Twilio `From` value is still passed
  to `processed_messages` (`from_number`); the normalized `+57...` phone is
  passed to `advance(from_number=...)`.
* All five `ConversationAdvancementOutcome` values
  (`TURN_APPENDED_INCOMPLETE`, `PARSE_INCOMPLETE`, `DRAFT_CREATED`,
  `ALREADY_HAS_DRAFT`, `DUPLICATE_MESSAGE`) map to the same `200` response with
  no outbound reply.
* When `result.resulting_order_id` is set (`DRAFT_CREATED` or
  `ALREADY_HAS_DRAFT`), `processed_messages.mark_order_created(...)` links the
  triggering `MessageSid` to that order, preserving the existing
  `processed_messages.resulting_order_id` linking behavior.
* Added `_get_conversation_advancement_service(app)`, which lazily builds
  `ConversationAdvancementService` from `PostgresStorage`'s session factory
  (`PostgresConversationStateStore`, `PostgresConversationOrderLookup`,
  `TenantScopedReadService`, `ParsingService`, `OrderService` with the existing
  lifecycle store), or accepts an injected service for tests.
* Rewrote `tests/test_web_twilio_webhook.py` (23 tests) covering: invalid
  signature does not call the service; duplicate `MessageSid` does not call
  the service; a new message calls `advance(...)` exactly once with the
  expected arguments; all five outcomes return `200` with no outbound and the
  expected `processed_messages.resulting_order_id` linkage; and end-to-end
  draft creation/follow-up linking against real `PostgresStorage` +
  `ConversationAdvancementService`.

#### Safety conclusions

* No parser prompt or `PROMPT_VERSION` change.
* No outbound replies, UI, auto-confirmation, payment gate, or media handling.
* No queue/worker.
* No session-expiry or draft-amendment behavior.
* No `StorageInterface` or schema/migration changes.
* `live_sheets` was not run.

#### Deferred

* `create_draft_from_inbound_message(...)` and `web/inbound.py` are now
  dead/unreferenced (only `_twilio_whatsapp_sender_to_phone` is still used).
  Left in place intentionally for a later cleanup slice, since
  `tests/test_architecture_boundaries.py` references `web/inbound.py` in its
  guard sets.
* Session-boundary / idle-expiry policy for genuinely new second orders.
* Draft amendment after `draft_created`.
* UI/status visibility for conversation state.

#### Verification

* `pytest tests/test_web_twilio_webhook.py -q` passed: `23 passed`.
* `pytest tests/test_conversation_advancement.py
  tests/test_architecture_boundaries.py -q` passed: `11 passed`.
* `pytest -q` passed: `566 passed, 29 deselected`.
* `ruff check src tests pages` passed.
* `python -m compileall src tests pages` passed.
* `git diff --check` passed with LF-to-CRLF warnings only.

### M9.2C - Conversation Advancement Service

Implemented in `87dcd7f feat(m9): add conversation advancement service`.

#### Delivered

* Added `src/duna_orders/services/conversation_advancement.py` with
  `ConversationAdvancementService.advance(...)`,
  `ConversationAdvancementOutcome` (`TURN_APPENDED_INCOMPLETE`,
  `PARSE_INCOMPLETE`, `DRAFT_CREATED`, `ALREADY_HAS_DRAFT`,
  `DUPLICATE_MESSAGE`), and `ConversationAdvancementResult`.
* Routing requires explicit `tenant_id` and uses
  `get_latest_session_for_customer(tenant_id, from_number)` before
  `get_or_create_open_session(...)`. If no latest session exists, it
  creates/gets an open session. If the latest session is `open`, it is
  reused. If the latest session is `draft_created`, the inbound message
  appends to that existing session and the service returns
  `ALREADY_HAS_DRAFT` instead of opening a new session or creating a second
  draft. Any other future session status raises `NotImplementedError` rather
  than inventing routing policy.
* Renders a deterministic transcript from canonical conversation turns and
  calls existing `ParsingService.parse(tenant_id=..., raw_message=transcript,
  products=...)`. Fetches products through
  `TenantScopedReadService.list_products(tenant_id=..., active_only=True)`.
  Does not change `ParserInterface`, the parser prompt, or `PROMPT_VERSION`.
* Completeness rule: draft creation proceeds only when the parsed request has
  at least one item, each item has `product_id`, each item has
  `quantity > 0`, and each `product_id` exists in the tenant-scoped active
  product list. A `ParserError` returns `TURN_APPENDED_INCOMPLETE`. A
  successful parse that fails the completeness rule returns
  `PARSE_INCOMPLETE`.
* Idempotency: `append_turn_if_new(...)` provides `message_sid` idempotency.
  For `open` sessions, the orphan-draft guard runs before the
  duplicate-`message_sid` early return: if `session.resulting_order_id` is
  already set, or `ConversationOrderLookup.get_order_by_conversation_id(...)`
  finds an existing order for the conversation, the service calls
  `mark_draft_created(...)` and returns `ALREADY_HAS_DRAFT`, even on a
  retried `message_sid`. Only if no orphan draft is found and the
  `message_sid` is a duplicate does the service return `DUPLICATE_MESSAGE`
  (no parser call, no draft creation).
* Draft creation sets `request.conversation_id`, calls
  `OrderService.create_draft(...)`, then calls `mark_draft_created(...)`, and
  returns `DRAFT_CREATED`.
* `IntegrityError` race recovery: if `create_draft(...)` raises
  `IntegrityError` on the unique non-null `conversation_id` constraint, the
  service looks up the existing order by `tenant_id` + `conversation_id`,
  calls `mark_draft_created(...)`, and returns `ALREADY_HAS_DRAFT` instead of
  re-raising or creating a duplicate draft.
* Post-`draft_created` behavior: a new `message_sid` appends a turn to the
  existing `draft_created` session and returns `ALREADY_HAS_DRAFT` without
  calling the parser, calling `OrderService.create_draft`, opening a new
  session, or creating a second draft. A duplicate `message_sid` on a
  `draft_created` session returns `DUPLICATE_MESSAGE`.
* Added `tests/test_conversation_advancement.py` (9 tests) using `MockParser`
  fixtures; no LLM dependency.
* Added `src/duna_orders/services/conversation_advancement.py` to the
  architecture boundary guard's enforced runtime-read modules.

#### Safety conclusions

* No webhook wiring, UI, bot replies, or outbound changes.
* No `ParserInterface`, parser prompt, or `PROMPT_VERSION` changes.
* No `StorageInterface` signature changes.
* No `OrderService` lifecycle/state transition or confirmation transaction
  changes.
* No draft amendment.
* No session expiry / new-order boundary policy.
* No queue/worker/callbacks, payment gate, or inbound media.
* `live_sheets` was not run.

#### Deferred

* M9.3 webhook wiring to call `ConversationAdvancementService`.
* Session-boundary / idle-expiry policy for genuinely new second orders.
* Draft amendment after `draft_created`.
* Parser prompt tuning, if real multi-turn transcript quality later requires
  it.
* UI/status visibility for conversation state, if needed later.

#### Verification

* `pytest tests/test_conversation_advancement.py -q` passed: `9 passed`.
* `pytest tests/test_conversation_advancement.py
  tests/test_conversation_state_store.py tests/test_architecture_boundaries.py
  -q` passed: `33 passed, 4 deselected`.
* `pytest -q` passed: `557 passed, 29 deselected`.
* `ruff check src tests pages` passed.
* `python -m compileall src tests pages` passed.
* `git diff --check` passed with LF-to-CRLF warnings only.

### M9.2C-0 - Latest customer conversation lookup

Implemented in `981604a feat(m9): add latest conversation session lookup`.

#### Delivered

* Added `ConversationStateStore.get_latest_session_for_customer(tenant_id,
  customer_phone)`.
* Implemented it in `PostgresConversationStateStore`.
* Returns the latest `ConversationSession` for a tenant/customer regardless of
  status, ordered deterministically by `last_message_at DESC, updated_at DESC,
  opened_at DESC, conversation_id DESC`.
* Returns `None` if no matching session exists.

#### Safety conclusions

* Read-only: does not create sessions, append turns, mark `draft_created`,
  call the parser, call `OrderService`, or touch `StorageInterface`.
* Requires explicit `tenant_id`.
* Matches `customer_phone` exactly as stored; no normalization.
* No migration needed.

#### Reason

* M9.2C must not call `get_or_create_open_session` blindly after a customer's
  latest session is `draft_created`. A post-`draft_created` message must
  attach to that existing latest session and return `ALREADY_HAS_DRAFT`, not
  create a new open session and not create a second draft.

#### Deferred

* True new-order session boundary / idle-expiry policy.
* M9.2C service implementation.
* Webhook, UI, and outbound remain untouched.

#### Verification

* `pytest tests/test_conversation_state_store.py -q` passed:
  `22 passed, 4 deselected`.
* `pytest -q` passed: `548 passed, 29 deselected`.
* `ruff check src tests pages` passed.
* `python -m compileall src tests pages` passed.
* `git diff --check` passed with LF-to-CRLF warnings only.

### M9.2B - Conversation draft-link schema and persistence

Implemented in `9677ded feat(m9): add conversation draft links`.

#### Delivered

* Added nullable `conversation_id` to `DraftOrderRequest`.
* Added nullable `conversation_id` to `Order`.
* `OrderService.create_draft` carries `request.conversation_id` into the
  created draft `Order`.
* Added nullable `orders.conversation_id` in Postgres.
* Added a one-order-row-per-non-null-`conversation_id` constraint/index
  (`uq_orders_conversation_id_not_null`). The constraint is global and not
  status-dependent; multiple `NULL` `conversation_id` orders remain allowed.
* Added a `tenant_id` + `conversation_id` lookup index
  (`ix_orders_tenant_id_conversation_id`).
* Added nullable `resulting_order_id` to `conversation_sessions`.
* Added `mark_draft_created(tenant_id, conversation_id, order_id)` to
  `ConversationStateStore`.
* Added `PostgresConversationOrderLookup` as a narrow helper outside
  `StorageInterface`.
* Carried nullable `conversation_id` across Postgres, memory, and
  Sheets-backed order paths; updated schema constants and tests.
* Updated the smoke preflight Alembic head expectation.

#### Safety conclusions

* M9.2B is schema/domain/persistence only.
* No parser imports or calls.
* No `PROMPT_VERSION` changes.
* No advancement service.
* No webhook wiring.
* No UI.
* No outbound/provider changes.
* No `StorageInterface` signature changes.
* No `OrderService` lifecycle/state transition changes.
* No confirmation transaction changes.
* No draft amendment behavior.
* No cross-store transaction.
* No parse-status persistence.
* No `resulting_order_id` on orders; `resulting_order_id` exists only on
  `conversation_sessions`.
* `PostgresConversationOrderLookup` is outside `StorageInterface`, requires
  `tenant_id` explicitly, finds by `tenant_id` + `conversation_id`, mutates
  nothing, and does not import or call `OrderService`.
* `mark_draft_created(...)` is tenant-scoped, sets `status="draft_created"`,
  sets `resulting_order_id`, increments `version` on first mark, is idempotent
  for the same order id, and conflicts on a different order id.
* The orphan-draft idempotency foundation is ready for M9.2C.

#### Sheets note

* `schema.py` and `sheets.py` were updated so the `orders` tab carries
  `conversation_id`.
* Existing live Sheets spreadsheets created before this change do not have a
  `conversation_id` header and may need a header migration before
  `live_sheets` is run against them.
* `live_sheets` was not run as part of this verification.

#### Verification

* `pytest tests/test_conversation_state_store.py tests/test_postgres_models.py tests/test_smoke_preflight.py -q`
  passed: `44 passed, 4 deselected`.
* `pytest -q` passed: `539 passed, 29 deselected`.
* `pytest -q -m live_postgres tests/test_conversation_state_store.py tests/test_postgres_live_smoke.py`:
  first run timed out at 124s; rerun passed: `6 passed, 13 deselected`.
* `ruff check src tests pages` passed.
* `python -m compileall src tests pages` passed.
* `alembic heads` reported `d6e7f8a9b0c1 (head)`.
* `alembic upgrade head` passed against configured Postgres.
* `git diff --check` passed with LF-to-CRLF warnings only.

### M9.2A - Conversation advancement service design refinement

Documented.

#### Delivered

* Added `docs/M9_2A_CONVERSATION_ADVANCEMENT_SERVICE_DESIGN.md`.
* Chose the orphan-draft idempotency strategy: nullable `conversation_id` on
  conversation-origin drafts plus a unique non-null order constraint.
* Defined recovery for the crash window after draft creation but before
  conversation marking.
* Defined the M9.2B schema/domain/persistence slice and the M9.2C advancement
  service slice.
* Defined the advancement service input, output, and outcome enum.
* Preserved parser prompt, `PROMPT_VERSION`, `StorageInterface`,
  `OrderService` lifecycle, confirmation transaction, webhook behavior, UI, and
  outbound/provider behavior.

#### Verification

* Documentation-only change.
* No code, tests, migrations, commit, or push.

### M9.1 - Conversation store foundation

Implemented in `e25634a feat(m9): add conversation state store`.

#### Delivered

* Added a narrow `ConversationStateStore` protocol outside `StorageInterface`.
* Added `PostgresConversationStateStore` as a persistence-only store.
* Added Postgres conversation state models:
  `conversation_sessions` and `conversation_turns`.
* Added Alembic migration
  `alembic/versions/2026_06_10_0003-add_conversation_state.py`.
* Added schema constants for conversation session and turn tables.
* Added store-only tests for idempotency, tenant isolation, turn ordering,
  reachable status, and session timestamp/version behavior.
* Added live Postgres coverage for concurrent open-session creation and
  concurrent duplicate turn append.
* Added metadata guard coverage for the new Postgres-only tables.
* Updated smoke preflight's Alembic head expectation.

#### Safety conclusions

* Store is persistence only.
* No parser imports or calls.
* No draft creation.
* No webhook wiring.
* No UI.
* No `StorageInterface` changes.
* No `OrderService` changes.
* No `PROMPT_VERSION` changes.
* No customer phone normalization or matching changes.
* No four-hour expiry logic in the store.
* No `accumulated_text`.
* No `resulting_order_id`.
* No `latest_parse_status` or `latest_parse_error`.
* No `mark_draft_created(...)` or `expire_session(...)` methods.
* Only `open` status is written/reachable by M9.1 store methods.
* Turns are the canonical transcript source for future M9.2 work.

#### Verification

* `pytest -q` passed: `519 passed, 25 deselected`.
* `pytest -q -m live_postgres tests\test_conversation_state_store.py tests\test_postgres_live_smoke.py`
  passed: `4 passed, 8 deselected`.
* `ruff check src tests` passed.
* `python -m compileall src tests` passed.
* `alembic upgrade head` passed against configured Postgres.
* `git diff --check` passed with LF-to-CRLF warnings only.

### M9.0 - Conversation state architecture design lock

Documented.

#### Delivered

* Added the M9 conversation state architecture design.
* Defined conversation state as a front-end intake stage that produces an
  existing operator-reviewable draft order.
* Locked `processed_messages.MessageSid` as the first idempotency gate for
  conversation advancement.
* Chose a narrow Postgres-backed conversation-state protocol outside
  `StorageInterface`.
* Required conversation turns as the canonical source of truth.
* Required `message_sid` uniqueness plus optimistic versioning or
  transaction-level locking for close-arriving same-customer turns.
* Preserved parser statelessness, `ParserInterface`, and `PROMPT_VERSION`.
* Preserved `OrderService` lifecycle, confirmation transaction, and
  outbound/provider behavior.
* Deferred automatic draft amendment after `draft_created`.

#### Verification

* Documentation-only change.
* No code, tests, migrations, commit, or push.

### M8.6.3E - Retry max-attempt enforcement

Implemented.

#### Delivered

* Added backend/store enforcement for a maximum of `2` total attempts per
  outbound acknowledgement row.
* Suppressed failed rows with `attempt_count >= 2` using
  `suppressed_retry_limit_reached`, even when `retry_failed=True`.
* Mapped max-attempt suppression to the UI-safe non-send result:
  `Acknowledgement was not sent. Manual follow-up is required.`
* Kept failed rows with `attempt_count < 2` retryable in Orders Today:
  `Acknowledgement was not sent. You can retry.` with
  `Retry acknowledgement`.
* Rendered failed rows with `attempt_count >= 2` as
  `Acknowledgement was not sent. Manual follow-up is required.` and hid
  `Retry acknowledgement`.
* Ensured max-attempt suppression does not call the adapter, does not create a
  new outbound row, and keeps row count at `1`.
* Preserved existing `unknown`, `sending`, `send_requested`, and `sent`
  suppression behavior.
* Kept `attempt_count`, failure time, provider errors, and provider internals
  hidden in the UI.

#### Verification

* Targeted tests passed: `95 passed in 18.57s`.
* `pytest -q` passed: `508 passed, 23 deselected in 49.83s`.
* `ruff check src tests pages` passed.
* `python -m compileall src tests pages` passed.
* `git diff --check` reported only LF-to-CRLF warnings.

#### Manual UI smoke

* Used the throwaway Neon branch only.
* DB helper seeded two today-visible confirmed orders without repo edits,
  service send path, Twilio call, or WhatsApp send.
* Guard row was present: `GUARD_ORDER_ID=ord_ui_dup_smoke_20260610`,
  `GUARD_STATUS=sent`.
* For `ord_ui_retry_limit_attempt1_smoke_20260610`,
  `OUTBOUND_STATUS=failed`, `ATTEMPT_COUNT=1`, and
  `OUTBOUND_ACK_ROW_COUNT=1`; Orders Today showed
  `Acknowledgement was not sent. You can retry.` and
  `Retry acknowledgement`.
* For `ord_ui_retry_limit_attempt2_smoke_20260610`,
  `OUTBOUND_STATUS=failed`, `ATTEMPT_COUNT=2`, and
  `OUTBOUND_ACK_ROW_COUNT=1`; Orders Today showed
  `Acknowledgement was not sent. Manual follow-up is required.` and hid
  `Retry acknowledgement` and `Send acknowledgement`.

#### Safety conclusions

* Backend/store/service is now the final authority for retry max attempts.
* Stale UI cannot bypass the max-attempt rule.
* Max-attempt suppression does not call the adapter.
* Max-attempt suppression does not create duplicate outbound rows.
* Manual follow-up state is provider-neutral.
* No delivery, receipt, read, or customer-notified wording was introduced.
* Full phone display in the Orders Today card remains existing page behavior and
  is not part of outbound acknowledgement leakage; masking can be considered in
  a future privacy/UX slice.

### M8.6.3C - Guarded retry execution smoke

Completed smoke validation.

#### Scope

* Performed smoke-only validation of the M8.6.3B retry UI against the
  throwaway Neon branch.
* Made no code changes.
* Used a safe operator-controlled WhatsApp recipient ending in `4241`.
* Verified the real retry execution path from Orders Today UI through the
  service, store, and Twilio.
* Confirmed retry reused the same outbound idempotency row.
* Confirmed no duplicate outbound row was created.
* Confirmed the WhatsApp message was received.

#### Smoke setup

* Confirmed the throwaway Neon branch through the known sent guard row.
* Safe recipient source was prior successful manual outbound smoke:
  `SAFE_SOURCE_ORDER_ID=demo_ord_01486`,
  `SAFE_SOURCE_CUSTOMER_NAME=Carlos Smoke`,
  `SAFE_SOURCE_MASKED_PHONE_LAST4=****4241`.
* Prepared confirmed retry execution order
  `ord_ui_retry_execution_smoke_20260610`.
* Prepared failed outbound row
  `out_ui_retry_execution_smoke_20260610` with initial `status=failed`,
  `OUTBOUND_ACK_ROW_COUNT=1`, `SERVICE_SEND_PATH_CALLED=false`, and
  `TWILIO_CALLED=false` during DB prep.

#### Manual Streamlit smoke

* Orders Today showed failed retry UI:
  `Acknowledgement was not sent. You can retry.` and `Retry acknowledgement`.
* First click opened explicit confirmation:
  `Send this acknowledgement again? The previous attempt failed.`
* Final confirmation executed the real retry.
* The safe test recipient received the WhatsApp message.

#### DB verification after retry

* `ORDER_ID=ord_ui_retry_execution_smoke_20260610`.
* `CUSTOMER_NAME=Carlos Smoke Retry Smoke`.
* `MASKED_CUSTOMER_PHONE_LAST4=****4241`.
* `OUTBOUND_ROW_COUNT=1`.
* `OUTBOUND_MESSAGE_ID=out_ui_retry_execution_smoke_20260610`.
* `STATUS=sent`.
* `ATTEMPT_COUNT=2`.
* `PROVIDER=twilio`.
* `PROVIDER_MESSAGE_ID_POPULATED=true`.
* `SENT_AT_POPULATED=true`.
* `LAST_ERROR_CODE=null`.
* `LAST_ERROR_MESSAGE=null`.
* `SAME_OUTBOUND_MESSAGE_ID_REUSED=true`.
* `ROW_COUNT_STAYED_1=true`.
* `ATTEMPT_COUNT_INCREASED_FROM_1=true`.
* `RETRY_EXECUTION_SMOKE_RESULT=PASS`.

#### Safety conclusions

* Retry execution works end to end.
* Same outbound row was reused.
* Row count stayed `1`.
* `attempt_count` increased from `1` to `2`.
* No provider secrets or full phone number were documented.
* No new behavior was implemented in this slice.

### M8.6.3B - Retry acknowledgement UI implementation

Implemented.

#### Delivered

* Added the smallest safe retry UI for outbound acknowledgements in Orders
  Today.
* Rendered retry only for outbound acknowledgement rows with `status=failed`.
* Updated failed-row text to
  `Acknowledgement was not sent. You can retry.`
* Added the `Retry acknowledgement` button for failed rows only.
* Required an explicit confirmation step before retry fires.
* Used the exact confirmation text:
  `Send this acknowledgement again? The previous attempt failed.`
* Routed confirmed retry through
  `OutboundAcknowledgementService.send_order_confirmed_acknowledgement(..., retry_failed=True)`.
* Kept the UI from calling the provider adapter directly.
* Kept the UI from creating outbound rows.
* Preserved backend claim/idempotency as the final send authority.
* Used rerun/re-query after retry action so stale failed/retryable display does
  not persist.

#### Non-retryable states

* `sent`: no retry.
* `sending`: no retry.
* `send_requested`: no retry.
* `unknown`: no retry.
* no outbound row: existing `Send acknowledgement` behavior remains.
* blocked or missing required details: no retry.
* disabled or not-ready outbound setup: no retry.

#### Manual UI smoke

* DB-only smoke helper seeded a failed-row smoke order on the throwaway Neon
  branch without calling the service or Twilio:
  `ord_ui_retry_failed_smoke_20260610`.
* Seeded row evidence:
  `OUTBOUND_MESSAGE_ID=out_ui_retry_failed_smoke_20260610`,
  `OUTBOUND_STATUS=failed`, `OUTBOUND_ACK_ROW_COUNT=1`,
  `SERVICE_SEND_PATH_CALLED=false`, `TWILIO_CALLED=false`.
* Manual Streamlit smoke passed: the failed row showed
  `Acknowledgement was not sent. You can retry.` and
  `Retry acknowledgement`.
* First click showed only the confirmation text:
  `Send this acknowledgement again? The previous attempt failed.`
* Final confirmation was not required for this UI-gate smoke.
* Regression checks passed: sent rows did not show retry, and no-record rows
  still showed `Send acknowledgement`, not `Retry acknowledgement`.

#### Verification

* Targeted tests passed: `89 passed`.
* `pytest -q` passed: `502 passed, 23 deselected`.
* `ruff check src tests pages` passed.
* `python -m compileall src tests pages` passed.
* `git diff --check` reported only LF-to-CRLF warnings.

#### Deferred

* No `attempt_count` display.
* No last failure time display.
* No auto-send on confirm.
* No delivery/read callbacks.
* No queue/worker behavior.
* No payment-dependent content.
* No parser behavior or `PROMPT_VERSION` changes.
* No `StorageInterface` extension.
* No `OrderService` coupling.

### M8.6.2A - New Order session-state initialization guard

Implemented.

#### Delivered

* Fixed a New Order page crash where `pages/1_New_Order.py` could read
  `st.session_state.catalog_ready` before ensuring the key existed.
* Added a missing-key guard for `catalog_ready`.
* Preserved an existing `catalog_ready` value when present.
* Initialized `catalog_ready` only when missing.
* Added regression coverage in `tests/test_new_order_session_state.py`.

#### Manual smoke

* Safe local Streamlit smoke passed with `DUNA_STORAGE_BACKEND=memory` and
  `DUNA_OUTBOUND_ENABLED=false`.
* The New Order page loaded without the missing `catalog_ready` crash.
* Successful order creation was not required for this smoke.

#### Verification

* `pytest tests/test_new_order_session_state.py -q` passed: `1 passed`.
* `pytest -q` passed: `490 passed, 23 deselected`.
* `ruff check src tests pages` passed.
* `python -m compileall src tests pages` passed.
* `git diff --check` reported only LF-to-CRLF warnings.

#### Explicitly not included

* No parser behavior changes.
* No `PROMPT_VERSION` changes.
* No outbound behavior changes.
* No Orders Today changes.
* No storage contract changes.
* No catalog, product, or order business-rule changes.

### M8.6.1D - Provider-neutral outbound unavailable UI messages

Implemented.

#### Delivered

* Updated Orders Today acknowledgement unavailable/not-ready rendering so
  provider-specific setup diagnostics are not shown to operators.
* Kept disabled outbound rendering exactly as
  `Outbound acknowledgement is disabled.`
* Mapped enabled-but-not-ready outbound setup to
  `Outbound acknowledgement is not fully configured.`
* Kept provider-specific setup diagnostics internal for developer/operator
  diagnostics.

#### Verification

* Targeted tests passed: `56 passed`.
* `pytest -q` passed: `489 passed, 23 deselected`.
* `ruff check src tests` passed.
* `python -m compileall src tests` passed.
* `git diff --check` reported only LF-to-CRLF warnings.

#### Deferred

* No send behavior changes.
* No adapter changes.
* No preflight changes.
* No parser behavior or `PROMPT_VERSION` changes.
* No `StorageInterface` extension.
* No `OrderService` coupling.

### M8.6.1C - Read-only manual acknowledgement status visibility

Implemented.

#### Delivered

* Added read-only outbound acknowledgement status visibility to Orders Today
  for confirmed orders.
* For no outbound row, Orders Today shows
  `No acknowledgement has been sent yet.` and shows `Send acknowledgement`.
* For a sent row, Orders Today shows `Acknowledgement was already sent.` and
  hides the send button.
* For `sending` or `send_requested`, Orders Today shows
  `Acknowledgement is being sent.` and hides the send button.
* For `unknown` or may-have-sent states, Orders Today shows
  `Acknowledgement status is unclear — it may already have been sent. Check before taking any action.`
  and hides the send button.
* For failed retryable state, Orders Today shows
  `Acknowledgement could not be sent. Retry is not available yet.` and hides
  the send button.
* For blocked or missing required details, Orders Today shows
  `Acknowledgement cannot be sent — order is missing required details.` and
  hides the send button.
* Preserved disabled/not-ready behavior.
* Kept UI status display-only; backend claim-before-send remains the final send
  authority.
* Kept the button routed through
  `OutboundAcknowledgementService.send_order_confirmed_acknowledgement(...)`.
* Kept provider internals out of the UI.

#### Manual UI smoke

* Disabled/outbound-off confirmed order smoke passed: Orders Today showed
  `Outbound acknowledgement is disabled.` and no `Send acknowledgement` button.
* Sent existing row smoke passed using order `ord_ui_dup_smoke_20260610` and
  outbound row `out_01ktr4e71rw6hqeadbyb5dwgq7`: Orders Today showed
  `Acknowledgement was already sent.` and no send button.
* No-record confirmed order smoke passed using order
  `ord_ui_no_record_smoke_20260610`, with `OUTBOUND_ACK_ROW_COUNT 0`: Orders
  Today showed `No acknowledgement has been sent yet.` and showed
  `Send acknowledgement`.

#### Verification

* `pytest -q` passed: `481 passed, 23 deselected`.
* `ruff check src tests` passed.
* `python -m compileall src tests` passed.
* `git diff --check` reported only LF-to-CRLF warnings.

#### Deferred

* No retry UI or resend button.
* No auto-send on confirm.
* No delivery/read callbacks.
* No queue/worker behavior.
* No `StorageInterface` extension.
* No `OrderService` coupling.

### M8.6.1B - Manual acknowledgement UI

Implemented.

#### Delivered

* Added a pure UI result mapper for outbound acknowledgement outcomes.
* Added UI setup/factory readiness for constructing the outbound
  acknowledgement service only when outbound is enabled, Postgres storage is in
  use, tenant binding is configured, and Twilio outbound settings are present.
* Added an operator-triggered acknowledgement section to Orders Today.
* Rendered the acknowledgement section only for confirmed orders.
* Showed a safe unavailable reason when outbound setup is not ready, without
  calling the service.
* Showed `Send acknowledgement` only when setup is available.
* Kept the service call behind an explicit operator button click.
* Mapped service results through the UI-safe outcome mapper and displayed by
  severity.
* Kept provider internals out of the UI.

#### Manual UI smoke

* Local safety smoke passed with `DUNA_STORAGE_BACKEND='memory'` and
  `DUNA_OUTBOUND_ENABLED=False`.
* Orders Today loaded with a memory/local confirmed test order.
* The confirmed card showed `Acknowledgement`.
* The visible safe message was `Outbound acknowledgement is disabled.`
* Visible buttons were `Start preparation`, `Cancel`, and `Refresh`.
* `Send acknowledgement` was not present, no provider internals were visible,
  and no send path was available.
* Initial Postgres duplicate-suppression UI smoke attempt failed safely because
  known sent smoke order `demo_ord_01486` was created on `2026-05-27` and was
  not visible in Orders Today for `2026-06-10`.
* During that safe-fail attempt, outbound row
  `out_01ktr15dq66n1q6x3v8atdwz6f` remained `sent`, kept
  `provider_message_id` populated, kept `attempt_count=1`, had no error fields,
  and no UI click or send attempt occurred.
* Postgres duplicate-suppression UI smoke passed on the throwaway Neon smoke
  branch using a today-visible seeded duplicate.
* Seeded confirmed order `ord_ui_dup_smoke_20260610` for tenant
  `el-fogon-colombiano` with `created_at=2026-06-10 06:45:09.634329+00`.
* Seeded sent outbound row `out_01ktr4e71rw6hqeadbyb5dwgq7` with
  `acknowledgement_type=order_confirmed_ack`, `status=sent`,
  `provider=twilio`, populated fake smoke `provider_message_id`,
  `attempt_count=1`, no error fields, and populated `sent_at`.
* Orders Today showed the seeded order and the buttons `Send acknowledgement`,
  `Start preparation`, `Cancel`, and `Refresh`.
* Clicking `Send acknowledgement` once displayed
  `Acknowledgement was already sent.`
* After the click, the outbound row count remained `1`, the same
  `outbound_message_id` remained `sent`, `attempt_count` stayed `1`, and no
  error fields were populated.
* No new WhatsApp send happened.
* Streamlit was stopped after both smokes and local settings were reset to
  `DUNA_STORAGE_BACKEND=memory` and `DUNA_OUTBOUND_ENABLED=false`.
* Focused verification passed with `62 passed`; ruff passed; git status was
  clean.

#### Deferred

* No retry UI.
* No auto-send on confirm.
* No inbound review changes.
* No coupling into `OrderService.confirm_approved_order` or the confirmation
  transaction.
* No `StorageInterface` extension.
* No parser behavior or `PROMPT_VERSION` changes.
* No delivery/read callbacks.
* No queue/worker behavior.
* No payment-dependent acknowledgement content.

### M8.6.1A - Outbound acknowledgement core

Implemented.

#### Delivered

* Added a deterministic Colombian-Spanish order-confirmed acknowledgement
  template.
* Added durable outbound acknowledgement persistence and idempotency keyed by
  `tenant_id + order_id + acknowledgement_type`.
* Added service orchestration for operator-triggered confirmed-order
  acknowledgements behind a fake-adapter-tested provider boundary.
* Restricted acknowledgements to confirmed orders.
* Enforced claim-before-send so an outbound row reaches `sending` before any
  provider adapter call.
* Enforced store state transitions so `mark_sent(...)`, `mark_failed(...)`,
  and `mark_unknown(...)` only update rows currently in `sending`.
* Kept `sending` and `unknown` as non-resendable may-have-sent states.
* Kept outbound persistence outside `StorageInterface`.
* Kept outbound service reads tenant-scoped and covered by the architecture
  boundary guard.
* Added the real Twilio outbound acknowledgement adapter behind the proven
  provider-neutral adapter boundary.
* Added env-gated outbound pilot configuration with outbound disabled by
  default.
* Added outbound smoke preflight checks for tenant binding, Twilio credentials,
  and WhatsApp sender identity.
* Added a guarded manual outbound smoke script and runbook for throwaway-branch
  verification only.
* Confirmed the adapter normalizes plain E.164 customer phone snapshots to
  `whatsapp:+...` when the configured sender is a WhatsApp channel address.

#### Manual outbound smoke

* Real Twilio adapter smoke passed on a throwaway Neon branch.
* Alembic upgraded successfully to head `a4b7c9d2e6f1`.
* Preflight passed with `SUMMARY: PASS (15/15 checks passed)`.
* Initial diagnostic attempts failed safely:
  * Twilio `20003` from placeholder/bad credentials;
  * Twilio `21910` from an invalid WhatsApp From/To channel pair.
* The WhatsApp channel mismatch was fixed by
  `c769dae fix(outbound): normalize WhatsApp recipient addresses`.
* After rejoining the Twilio WhatsApp Sandbox and using fresh confirmed order
  `demo_ord_01486`, the real WhatsApp acknowledgement arrived.
* The successful `outbound_messages` row was:
  * `outbound_message_id=out_01ktr15dq66n1q6x3v8atdwz6f`;
  * `tenant_id=el-fogon-colombiano`;
  * `order_id=demo_ord_01486`;
  * `acknowledgement_type=order_confirmed_ack`;
  * `status=sent`;
  * `provider=twilio`;
  * `provider_message_id` populated;
  * `attempt_count=1`;
  * `last_error_code=null`;
  * `last_error_message=null`;
  * `sent_at` populated.
* Duplicate suppression passed:
  * service outcome `suppressed_duplicate`;
  * reason `Acknowledgement was already sent.`;
  * `attempted=False`;
  * `sent=False`;
  * same `outbound_message_id`;
  * status remained `sent`;
  * `provider_message_id` remained populated;
  * `attempt_count` stayed `1`;
  * no second row and no second send side effect.
* Local `.env` was reset after smoke:
  * `DUNA_STORAGE_BACKEND=memory`;
  * `DUNA_OUTBOUND_ENABLED=false`.
* The throwaway Neon branch is being kept temporarily and will auto-delete
  later.

#### Deferred

* No UI or Streamlit changes.
* No coupling into the confirmation transaction or `OrderService`.
* No parser behavior or `PROMPT_VERSION` changes.
* No `StorageInterface` extension.
* No auto-send on confirm.
* No queue/worker behavior.
* No delivery/read callbacks. Twilio API acceptance is not proof of delivery or
  read status.
* No payment-dependent acknowledgement content.

### M8.5 Stage 2B-2 - Unscoped broad-read naming

Implemented.

#### Delivered

* Renamed broad storage product and customer list reads to
  `unscoped_list_products(...)` and `unscoped_list_customers(...)`.
* Kept `TenantScopedReadService.list_products(...)` and
  `TenantScopedReadService.list_customers(...)` stable as scoped APIs.
* Updated the Stage 2A architecture guard to forbid both old product/customer
  broad-read names and the new `unscoped_` names.
* Kept no deprecated aliases.

#### Deferred

* No `get_order(...)`, `list_orders(...)`, or `list_stock_movements(...)`
  rename.
* No write-path tenant scoping.
* No parser or diagnostic behavior change.

### M8.5 Stage 2A - Runtime read guard and diagnostic naming

Implemented.

#### Delivered

* Added a static architecture boundary test for the Stage 1 runtime read
  modules.
* Routed inbound review's intentional broad diagnostic order lookup through
  `DiagnosticReadService.get_order_for_diagnostics(...)`.
* Marked deferred `OrderService` write/action broad order reads with a
  consistent greppable marker.
* Documented that the guard covers runtime read paths only, not write-path
  tenant safety.

#### Deferred

* No broad-read renaming yet.
* No `StorageInterface` change.
* No write-path tenant scoping.

### M8.5D-F - Stage 1 scoped-read caller migrations

Closed.

#### Delivered

* M8.5D migrated Orders Today away from direct broad `storage.list_orders()` calls.
* Orders Today now uses `TenantScopedReadService.list_orders(tenant_id=...)`.
* Orders Today today-only filtering, completed/cancelled toggle behavior, lifecycle actions, action-service tenant checks, and UI layout were preserved.
* M8.5E migrated New Order product reads away from direct broad `storage.list_products(...)` calls.
* New Order parser product context, manual product selector, and inventory table now use `TenantScopedReadService.list_products(tenant_id=...)`.
* New Order preserved `active_only=True` for parser context and manual selector products.
* New Order preserved `active_only=False` for the inventory table.
* New Order parser behavior and `PROMPT_VERSION` were unchanged.
* M8.5F migrated runtime inbound parsing away from broad `storage.list_products(...)` plus manual tenant filtering.
* Runtime inbound parsing now uses `TenantScopedReadService.list_products(tenant_id=..., active_only=True)`.
* Runtime inbound preserved Twilio signature validation, `MessageSid` idempotency, duplicate/empty-body behavior, `ParsingService.parse(...)`, draft request normalization, `OrderService.create_draft(...)`, `mark_order_created(...)`, and `PROMPT_VERSION`.
* Added a focused webhook test proving another tenant's active product is excluded from inbound parser context.

#### Stage 1 status

`TenantScopedReadService` is now used by:

* dashboard read scenario;
* Orders Today;
* New Order product reads;
* runtime inbound parser product context.

#### Verification

* `python -m compileall src tests pages` passed.
* `pytest -q` -> 368 passed, 23 deselected.
* `ruff check src tests pages` passed.

#### Explicitly not included

* No `StorageInterface` change.
* No broad-read quarantine.
* No Stage 2 guard tests.
* No schema or migration changes.
* No write-path tenant scoping.
* No tenant ID request-context or runtime resolution design.

### M8.5C - Tenant-scoped read proof-of-use closeout

Closed.

#### Delivered

* Implemented the Stage 1 proof-of-use from `docs/M8_5B_TENANT_SCOPED_READS_DESIGN.md`.
* Added `TenantScopedReadService` as a thin read-only layer above the unchanged `StorageInterface`.
* Required explicit keyword-only `tenant_id` with no default for `list_orders(...)`, `get_order(...)`, `list_products(...)`, and `list_customers(...)`.
* Empty or whitespace `tenant_id` now raises `ValueError` at the scoped read boundary.
* The scoped layer delegates to existing broad reads and filters internally by tenant.
* Added no SQLAlchemy, Google Sheets, Streamlit, FastAPI, or backend-specific imports to the scoped read layer.
* Migrated only `run_locked_dashboard_read_scenario(...)` as the proof-of-use caller.
* Kept the dashboard scenario public signature and metric semantics unchanged.

#### Tests

* Added tenant-isolation coverage for orders, order detail tenant mismatch, products, and customers.
* Added required-tenant coverage for blank, whitespace, and omitted `tenant_id`.
* Proved memory and SQLite-backed Postgres parity for the scoped layer.
* Covered `list_orders(...)` status/since filter preservation.
* Covered `list_products(...)` `active_only` filter preservation.
* Covered dashboard scenario exclusion of other-tenant rows.
* Preserved single-tenant dashboard scenario behavior.

#### Verification

* `python -m compileall src tests pages` passed.
* `pytest -q` -> 367 passed, 23 deselected.
* `ruff check src tests pages` passed.

#### Explicitly not included

* No `StorageInterface` change.
* No broad-read quarantine.
* No Stage 2 guard tests.
* No schema or migration changes.
* No write-path tenant scoping.
* No tenant ID request-context or runtime resolution design.
* No dashboard redesign.

### M8.5A - Postgres storage parity and hardening closeout

Closed.

#### Delivered

* Inspected Postgres storage parity now that inbound review, processed messages, atomic confirmation, lifecycle, and stock movement integrity depend on Postgres as the serious runtime path.
* Confirmed Postgres implements the current `StorageInterface`.
* Confirmed Postgres-only capabilities remain outside `StorageInterface`: processed messages, order lifecycle store, atomic approved confirmation, and demo/bulk helpers.
* Confirmed runtime construction uses `DUNA_STORAGE_BACKEND=postgres` and cached per-URL session factories.
* Kept atomic confirmation as a narrow Postgres capability instead of introducing a broad transaction abstraction.
* Hardened atomic confirmation so a SQLAlchemy `IntegrityError` during sale stock movement insert/flush maps to `DuplicateStockMovementError`.
* Kept the duplicate movement mapping narrow to the sale movement flush phase.
* Confirmed duplicate movement conflicts still fail hard and roll back without stock decrement, status update, or lifecycle transition.
* Added processed-message tests documenting that `mark_order_created(...)` is keyed by globally unique `message_sid`, missing message SIDs raise `ValueError`, and tenant scoping is enforced by read paths such as `get_message_for_order(...)`.

#### Deferred

* Broad storage reads remain mostly ID/global-list oriented by the current `StorageInterface`.
* Future multi-tenant runtime hardening may need tenant-scoped read services or `StorageInterface` evolution.
* Tenant-scoped broad-read hardening should go to Claude review before implementation.

#### Architecture boundaries preserved

* No `StorageInterface` broadening.
* No schema or migration changes.
* No lifecycle-rule changes.
* No cancellation stock reversal.
* No duplicate movement repair or idempotency.
* No outbound/customer messaging.
* No payment status enforcement.
* No inbound media/comprobante handling.
* No parser behavior changes or `PROMPT_VERSION` bump.
* No dashboard redesign.
* No broad transaction abstractions.

### M8.4 - Inbound review operator hardening closeout

Closed.

#### Delivered

* M8.4A hardened the inbound review page so list loading, draft review actions, and approved confirmation actions no longer display raw exception text to operators.
* Known inbound review failures now map to operator-facing messages for stale status, missing linked order, insufficient stock, missing product, duplicate or existing stock movement, unsupported backend, and generic action/list-load fallback cases.
* M8.4B added a service-level inbound review snapshot that returns draft items, approved items, and linked-message diagnostics in one service-owned summary.
* The inbound review page now receives review queues and diagnostics from the service instead of making diagnostic business decisions in Streamlit.
* Linked processed messages skipped because their linked order is missing, tenant-mismatched, confirmed, cancelled, or otherwise non-reviewable now surface through safe aggregate diagnostics.
* Diagnostic copy avoids raw order IDs, message SIDs, SQL, tracebacks, and raw exception text.
* Draft orders and Approved orders remain separate operator sections.

#### Architecture boundaries preserved

* M8.4A made no service, storage, or lifecycle business-rule changes.
* M8.4B added narrow review-service diagnostics only.
* No `StorageInterface` broadening.
* No schema or migration changes.
* No lifecycle-rule changes.
* No parser behavior changes or `PROMPT_VERSION` bump.
* No outbound/customer messaging.
* No payment status enforcement.
* No inbound media/comprobante handling.
* No cancellation stock reversal.
* No duplicate movement repair or idempotency.
* No auto-confirmation.
* No queue or worker behavior.
* No dashboard redesign.
* No broad transaction abstractions.

#### Explicitly not included

* Unlinked/no-result processed messages remain intentionally invisible in this slice.
* Parse failures remain out of scope.
* No parse-log, timestamp-proximity, reparse, or parser behavior was added.

### Manual Verification

* M8.3.1C manual operator-confirmation smoke passed at baseline `3c37926` using a process-level `DUNA_STORAGE_BACKEND=postgres` override against Postgres tenant `el-fogon-colombiano`.
* Confirmed linked approved inbound order `ord_01ktjxxdpesn3tc5by46hhz5v1` through the Streamlit inbound review UI harness after checking the inventory-commit gate.
* Verified `approved -> confirmed`, `confirmed_at` set, lifecycle source `operator`, two sale stock movements, stock decrements from `38 -> 37` for `bebida-limonada-natural` and `20 -> 18` for `plato-bandeja-paisa`, and removal from approved/draft review lists.
* Verified no outbound/customer message, payment behavior, inbound media, parser/reparse, dashboard behavior, stock reversal, or cancellation behavior occurred.

* M8.2.1C manual operator review UI smoke passed with no code changes at baseline `6d7673c`.
* Verified memory and Sheets backends show the Postgres-only unavailable state for inbound draft review.
* Verified Postgres tenant `el-fogon-colombiano` displayed linked draft `ord_01ktjxxdpesn3tc5by46hhz5v1` with raw inbound message, parsed items/modifiers, and COP total `$85.000`.
* Verified approving the draft moved `draft -> approved`, appended lifecycle transition source `operator`, removed the draft from the review list, and did not set `confirmed_at`, create stock movements, mutate product stock, or trigger outbound behavior.
* Reject smoke was not run because no second linked draft remained; no smoke data was created without approval.

* Manual inbound WhatsApp smoke passed on throwaway Neon branch `smoke-inbound-2026-06-07`.
* Verified happy-path draft order creation, duplicate `MessageSid` idempotency, and missing/tampered Twilio signature rejection.
* Final smoke counts were `orders_total=1501`, `processed_messages_total=2`, `order_status_transitions_total=1`, and `parse_log_total=2`.
* Created order `ord_01ktjxxdpesn3tc5by46hhz5v1` for tenant `el-fogon-colombiano` with status `draft`, pickup fulfillment, Nequi payment, and total `85000`.
* Verified `processed_messages` captured full `raw_body` and `resulting_order_id` for the successful message.
* Verified `parse_log` captured successful Claude output.
* The throwaway Neon branch was not manually deleted during the session and was left to auto-delete in 7 days.

### Explicitly not included

* No production or keeper Neon branch smoke was run.
* No outbound WhatsApp replies were added.
* No conversation state was added.
* No auto-confirmation was added.

## M8.1.4 - Deployment smoke local + tunnel for inbound webhook

Closed.

### Delivered

* Added a lifecycle-store guardrail test confirming injected lifecycle stores own sanctioned lifecycle status mutations instead of direct `storage.update_order_status` calls.
* Documented `PostgresStorage.update_order_status` as low-level persistence only; application lifecycle transitions must use `OrderService` with `PostgresOrderLifecycleStore` when transition rows are required.
* Added read-only `scripts/smoke_preflight.py` for local deployment smoke readiness checks.
* The preflight validates Postgres backend configuration, required Twilio/webhook settings, HTTPS webhook URL shape, database connectivity, and Alembic current-vs-head state.
* The preflight reports migration state only. It does not run upgrades and prints `alembic upgrade head` when the database is behind head.
* Added deterministic SQLite-backed tests for the smoke preflight. No live Neon, Twilio, or tunnel access is required.
* Added `docs/SMOKE.md` with the manual local FastAPI plus cloudflared tunnel smoke runbook.
* Added `docs/SMOKE_CHECKLIST.md` as the manual smoke execution checklist and pass/fail sheet.
* Documented that the local+tunnel smoke was not live-run by ChatGPT; Carlos will run it manually later.

### Explicitly not included

* No live Twilio smoke run.
* No live tunnel smoke run.
* No Neon auto-upgrade.
* No commits or pushes.

## M8.1.3 - Order lifecycle transition timestamps

Closed.

### Delivered

* Added append-only `order_status_transitions` table for order lifecycle event capture.
* Added domain model `OrderStatusTransition`.
* Added transition source field with `system` and `operator`.
* Added Alembic migration `d2f7b8a4c901`.
* Added `PostgresOrderLifecycleStore` as a dedicated lifecycle persistence concern.
* Kept `StorageInterface` unchanged.
* Kept storage pure: `OrderService` decides when a transition happens; lifecycle storage persists the already-decided transition.
* Added atomic Postgres persistence methods for:
  * order creation + initial transition;
  * status update + transition append.
* Added initial lifecycle capture for new drafts:
  * `from_status = NULL`;
  * `to_status = draft`;
  * `source = system`.
* Added transition capture for `confirm_order`:
  * `draft -> confirmed`;
  * `source = operator`.
* Added transition capture for `transition_order_status`:
  * subsequent operator-driven lifecycle changes append one transition each.
* Wired Streamlit `get_order_service(...)` to inject `PostgresOrderLifecycleStore` when the backend is Postgres.
* Wired the FastAPI Twilio inbound path to inject the lifecycle store for Postgres draft creation.
* Confirmed rejected transitions write no transition.
* Confirmed a failed atomic lifecycle-store write rolls back the status update.
* Confirmed current dashboard query-budget test still passes; the transition log is not read by the current dashboard widgets.
* Existing orders are not backfilled. Their full transition history was not captured before M8.1.3 and cannot be reconstructed honestly.

### Verification

* `pytest tests/test_order_lifecycle_store.py -q` -> 4 passed.
* `pytest tests/test_orders_service.py -q` -> 31 passed.
* `pytest tests/test_postgres_models.py tests/test_order_lifecycle_store.py tests/test_orders_service.py tests/test_ui_setup.py tests/test_web_twilio_webhook.py tests/test_postgres_dashboard_query_budget.py -q` -> 76 passed.
* `pytest -q` -> 279 passed, 23 deselected.
* `ruff check` on touched domain/service/storage/web/ui/test files -> All checks passed.
* `git diff --check` -> clean.
* Fresh SQLite migration check:

  * `alembic upgrade head` -> reached `d2f7b8a4c901`;
  * `alembic downgrade b7f4c8e2a901` -> passed;
  * `alembic upgrade head` -> reached `d2f7b8a4c901` again.

### Explicitly not included

* No outbound WhatsApp replies.
* No TwiML reply body.
* No conversation state machine.
* No auto-confirmation.
* No queue or async worker.
* No parser or LLM changes.
* No cancellation reason.
* No edit diff tracking.
* No synthetic backfill for existing orders.
* No dashboard widget reads from `order_status_transitions`.
* No Neon/runtime database upgrade in this slice.
## M8.1.2 - Raw inbound message capture

Closed.

### Delivered

* Replaced persisted `processed_messages.body_preview` with `processed_messages.raw_body`.
* Preserved the full Twilio inbound `Body` text before parsing, without trimming or truncation.
* Kept `from_number` capture unchanged on the insert-first idempotency path.
* Kept `received_at` as server receipt time.
* Did not add `wa_timestamp` because the standard Twilio inbound WhatsApp webhook payload does not provide a reliable original device send-time.
* Added Alembic migration `b7f4c8e2a901`.
* Used add-then-drop migration semantics instead of renaming `body_preview` to `raw_body`.
* Left existing migrated rows with `raw_body = NULL` rather than backfilling from already-truncated `body_preview`.
* Updated `PostgresProcessedMessageStore` and webhook ingestion to write `raw_body` in the same insert-first idempotency write.
* Confirmed successful inbound order creation links `processed_messages.resulting_order_id`.
* Confirmed empty-body messages store `raw_body = ""` or the exact inbound whitespace string and remain deduped.
* Confirmed parser failures preserve `raw_body`, return `200`, and create no order.
* Confirmed retries of captured messages remain deduped and do not overwrite raw event fields.

### Verification

* `pytest tests/test_web_twilio_webhook.py tests/test_processed_messages.py -q` -> 16 passed.
* `pytest tests/test_web_twilio_webhook.py tests/test_processed_messages.py tests/test_postgres_models.py -q` -> 25 passed.
* `Get-ChildItem src,tests -Recurse -File | Select-String -Pattern "body_preview"` -> no remaining source/test references.
* Fresh SQLite migration check:

  * `alembic upgrade head` -> reached `b7f4c8e2a901`;
  * `alembic downgrade 9c7e1f4a2b30` -> passed;
  * `alembic upgrade head` -> reached `b7f4c8e2a901` again.

### Explicitly not included

* No outbound WhatsApp replies.
* No TwiML reply body.
* No conversation state machine.
* No auto-confirmation.
* No queue or async worker.
* No parser or LLM changes.
* No `StorageInterface` changes.
* No parse latency, token, or cost fields.
* No per-state-transition timestamps.
## M8.1.1 - Twilio signature hardening and inbound idempotency

Closed.

### Delivered

* Hardened Twilio signature validation to require the configured public webhook URL.
* Removed fallback validation against reconstructed `request.url`.
* Kept validation fail-closed when `TWILIO_WEBHOOK_PUBLIC_URL` is missing.
* Confirmed full parsed Twilio POST form params are passed into Twilio `RequestValidator`.
* Confirmed `From` and `Body` extraction is separate from signature validation input.
* Confirmed webhook package uses `src/duna_orders/web/__init__.py`.
* Added Postgres-only `processed_messages` table for inbound Twilio idempotency.
* Added Alembic migration `9c7e1f4a2b30`.
* Added `PostgresProcessedMessageStore`.
* Added insert-first duplicate detection on `MessageSid`.
* Duplicate `MessageSid` requests return `200` without parsing or creating another draft.
* Empty-body signed requests are recorded by `MessageSid` and deduped on retry.
* Successful draft creation links `processed_messages.resulting_order_id`.

### Verification

* `pytest tests/test_web_twilio_webhook.py -q` -> 11 passed.
* `pytest tests/test_processed_messages.py -q` -> 4 passed.
* `pytest tests/test_web_twilio_webhook.py tests/test_processed_messages.py -q` -> 15 passed.
* `pytest tests/test_processed_messages.py tests/test_postgres_models.py tests/test_alembic_scaffold.py -q` -> 18 passed.
* `ruff check` on touched web/storage/test files -> All checks passed.
* `git diff --check` -> clean.
* Fresh SQLite migration check:

  * `alembic upgrade head` -> reached `9c7e1f4a2b30`;
  * `alembic downgrade aec69eff0019` -> passed;
  * `alembic upgrade head` -> passed again.

### Explicitly not included

* No outbound WhatsApp replies.
* No TwiML reply body.
* No conversation state machine.
* No auto-confirmation.
* No queue or async worker.
* No new parser or LLM path.
* No `StorageInterface` changes.

## M8.1 - FastAPI Twilio inbound webhook skeleton

Closed.

### Delivered

* Added a separate FastAPI webhook app under `src/duna_orders/web`.
* Added `GET /health`.
* Added `POST /webhooks/twilio/whatsapp` for Twilio WhatsApp inbound webhooks.
* Added Twilio signature validation using Twilio's `RequestValidator`.
* Validated the signature before parsing, storage access, or draft creation.
* Parsed Twilio `application/x-www-form-urlencoded` payloads.
* Extracted inbound `From` and `Body`.
* Routed inbound message text through the existing parser and draft creation path:

  * `ParsingService.parse(...)`;
  * `OrderService.create_draft(...)`.
* Created draft orders only; no auto-confirmation.
* Returned an empty `200` response for accepted webhooks so Twilio does not retry.
* Added graceful empty-body handling: signed empty messages return `200` and create no order.
* Added settings for Twilio auth token, optional public webhook URL, and configured webhook tenant.
* Added dependencies for FastAPI, Uvicorn, and Twilio.

### Verification

* `pytest tests/test_web_twilio_webhook.py -q` -> 5 passed.
* `ruff check src\duna_orders\web tests\test_web_twilio_webhook.py` -> All checks passed.

### Explicitly not included

* No outbound WhatsApp replies.
* No TwiML reply body beyond empty success response.
* No conversation state machine.
* No auto-confirmation.
* No queue or async worker.
* No new parser or LLM path.
* No `StorageInterface` changes.
* No FastAPI deployment configuration.

## M8.1C-3C - Postgres dashboard parity and query-budget assertion

Closed.

### Delivered

* Added a Postgres dashboard query-budget test using a real SQLAlchemy engine.
* Counted only SQL `SELECT` statements through SQLAlchemy `before_cursor_execute`.
* Drove the budget through the locked dashboard scenario and the same dashboard compute functions used by the Streamlit dashboard page.
* Confirmed `PostgresStorage.list_orders()` already uses `selectinload(OrderRow.items)`, so order items load through one bounded secondary `SELECT` instead of N+1 lazy loading.
* Locked the deterministic small dashboard scenario to `<= 4` SQL `SELECT` statements:
  * orders query;
  * one bounded order-items `selectinload` query;
  * customers query;
  * products query.
* Live Neon full-demo diagnostics observed `select_count: 6` for 1500 orders:
  * 1 orders query;
  * 3 bounded order-items `selectinload` batch queries;
  * 1 customers query;
  * 1 products query.
* The extra order-item queries are accepted because they are bounded `selectinload` batching, not N+1 lazy loading.
* Kept the existing Sheets read-budget test unchanged.

### Verification
* Manual Streamlit check with `DUNA_STORAGE_BACKEND=postgres`, Neon `DATABASE_URL`, and `DASHBOARD_TARGET=demo` rendered all 8 dashboard widgets with populated demo data.
* Live Neon dashboard compute diagnostic observed `select_count: 6` for the full 1500-order demo dataset.
* `pytest tests/test_postgres_dashboard_query_budget.py -q` -> 1 passed.
* `pytest tests/test_postgres_dashboard_query_budget.py tests/test_sheets_read_budget.py tests/test_dashboard_widgets.py tests/test_dashboard_page.py -q` -> 45 passed.

### Explicitly not included

* No FastAPI, Twilio, queue, conversation-state, LLM, or outbound messaging.
* No `StorageInterface` changes.
* No deletion of Sheets read-budget coverage.
* No new models or migrations.
## M8.1C-3B - Per-process Postgres engine cache

Closed.

### Delivered

* Added a framework-neutral process-level SQLAlchemy engine cache for Postgres.
* Added `get_or_create_engine(...)` keyed by `DATABASE_URL`.
* Added `get_or_create_session_factory(...)` so storage construction can reuse one session factory per cached engine.
* Added `dispose_all_engines()` and `reset_engine_cache()` for test isolation and clean shutdown hooks.
* Guarded cache check-and-create with a `threading.Lock`.
* Updated the storage factory so `DUNA_STORAGE_BACKEND=postgres` reuses the cached session factory instead of creating a new engine per `PostgresStorage` instance.
* Preserved lazy construction: building the engine/session factory/storage does not open a database connection.

### Explicitly not included

* No dashboard/Postgres parity work.
* No Postgres dashboard query-budget assertion.
* No Streamlit page changes or `st.session_state` changes.
* No `st.cache_resource`.
* No Sheets read-budget or `sheets_request_context` changes.
* No FastAPI, Twilio, queue, session lifecycle, LLM, outbound messaging, models, or migrations.
## M8.1C-3B - Per-process Postgres engine cache

Closed.

### Delivered

* Added a framework-neutral process-level SQLAlchemy engine cache for Postgres.
* Added `get_or_create_engine(...)` keyed by `DATABASE_URL`.
* Added `get_or_create_session_factory(...)` so storage construction can reuse one session factory per cached engine.
* Added `dispose_all_engines()` and `reset_engine_cache()` for test isolation and clean shutdown hooks.
* Guarded cache check-and-create with a `threading.Lock`.
* Updated the storage factory so `DUNA_STORAGE_BACKEND=postgres` reuses the cached session factory instead of creating a new engine per `PostgresStorage` instance.
* Preserved lazy construction: building the engine/session factory/storage does not open a database connection.

### Explicitly not included

* No dashboard/Postgres parity work.
* No Postgres dashboard query-budget assertion.
* No Streamlit page changes or `st.session_state` changes.
* No `st.cache_resource`.
* No Sheets read-budget or `sheets_request_context` changes.
* No FastAPI, Twilio, queue, session lifecycle, LLM, outbound messaging, models, or migrations.
## M8.1C-2 - Storage factory and Postgres backend selection

Closed.

### Delivered

* Added a UI-independent storage factory.
* Preserved existing `DUNA_STORAGE_BACKEND=memory` and `DUNA_STORAGE_BACKEND=sheets` behavior.
* Added `DUNA_STORAGE_BACKEND=postgres` to build `PostgresStorage` from `DATABASE_URL`.
* Kept the default backend as `memory`.
* Kept Postgres construction lazy; storage construction does not connect to the database.
* Updated `get_storage()` to delegate to the storage factory.
* Added factory-level tests for memory, sheets runtime/demo targets, postgres, missing `DATABASE_URL`, and invalid backend values.
* Added UI setup coverage for postgres backend selection.

### Explicitly not included

* No dashboard/Postgres parity work.
* No Streamlit page changes beyond `get_storage()` delegation.
* No runtime `sqlite` backend.
* No engine/pool lifecycle optimization.
* No webhook, Twilio, queue, session lifecycle, LLM, or outbound messaging.
## M8.1C-1B - Postgres demo reseed with bulk helpers

Closed.

### Delivered

* Added Postgres-specific bulk seeding helpers for products, customers, orders, and order items.
* Kept bulk helpers outside `StorageInterface`; they are trusted seeding/migration utilities only.
* Added tenant-scoped wipe behavior across products, customers, orders, order items, stock movements, and parse logs.
* Added a mandatory `tenant_id` guard for tenant-scoped delete operations.
* Added atomic `PostgresStorage.reseed_demo_dataset(...)` orchestration.
* Added `scripts/reseed_postgres.py` as a thin `DATABASE_URL`-driven CLI wrapper.
* Added non-live SQLite-backed reseed tests.
* Added live Neon reseed coverage behind the `live_postgres` marker.

### Verification

* `pytest tests/test_postgres_reseed.py -q`
* `pytest tests/test_postgres_reseed.py -q -m live_postgres`
* `python scripts/reseed_postgres.py`
* `pytest -q`
* `ruff check` on touched files
* `git diff --check`

### Explicitly not included

* No runtime backend selection.
* No Streamlit/Postgres wiring.
* No dashboard changes.
* No new models or migrations.
* No FastAPI webhook.
* No Twilio.
* No queue.
* No session lifecycle.
* No LLM or outbound messaging.
## M8.1C-0 - Live Postgres verification harness

Closed.

### Delivered

* Added `live_postgres` as an opt-in pytest marker.
* Kept live Postgres tests excluded from the default test run.
* Added a live Postgres smoke test for Alembic `upgrade head`.
* Added a live Postgres smoke test for the current `PostgresStorage` product, customer, and order flow.
* Documented `DATABASE_URL` in `.env.example`.
* Verified the current migration and storage layer against Neon Postgres.

### Verification

* `python -c "from duna_orders.config import settings; print('DATABASE_URL configured:', bool(settings.database_url)); print('Host/db:', settings.database_url.split('@')[-1] if settings.database_url else None)"` -> configured against Neon.
* `pytest tests/test_postgres_live_smoke.py -q` -> 2 deselected.
* `pytest tests/test_postgres_live_smoke.py -q -m live_postgres` -> 2 passed.
* `pytest tests/test_alembic_scaffold.py tests/test_postgres_storage_products_customers.py tests/test_postgres_storage_orders.py -q` -> 17 passed.
* `git diff --check` -> clean.

### Explicitly not included

* No runtime backend selection.
* No Streamlit wiring to Postgres.
* No dashboard changes.
* No demo reseeding into Postgres.
* No FastAPI webhook.
* No Twilio.
* No queue.
* No session lifecycle.
* No LLM or outbound messaging.

## M8.1A - Postgres foundation
Closed.

### Delivered

* Added SQLAlchemy 2.0 foundation.
* Added Alembic migration scaffold.
* Added `psycopg[binary]` for future Postgres connectivity.
* Added `ruff` to development dependencies.
* Added `database_url` to project settings.
* Added shared SQLAlchemy declarative `Base` with stable naming conventions.
* Added Postgres session utilities:

  * `make_engine(...)`;
  * `make_session_factory(...)`;
  * `session_scope(...)`.
* Added Alembic configuration using project settings instead of a hardcoded database URL.
* Connected Alembic autogenerate metadata to `Base.metadata`.
* Enabled Alembic comparison for column types and server defaults.
* Added scaffold tests that do not require a real Postgres server.
* Removed generated `src/duna_orders.egg-info/*` artifacts from Git tracking.
* Ignored future `*.egg-info/` generated folders.
* Removed an unrelated unused import found by the wider Ruff check.

### Verification

* `pytest tests/test_alembic_scaffold.py -q` -> 5 passed.
* `pytest tests/test_postgres_foundation.py -q` -> 4 passed.
* `pytest tests/test_storage_contract.py -q` -> 15 passed, 15 deselected.
* `alembic history` -> no revisions, no error.
* `ruff check src\duna_orders\storage alembic tests\test_alembic_scaffold.py tests\test_postgres_foundation.py` -> all checks passed.
* `git diff --check` -> clean.
* `git status --short` -> clean.

### Explicitly not included

* No SQLAlchemy table models.
* No migrations.
* No `PostgresStorage` implementation.
* No real Postgres connection.
* No runtime backend selection changes.
* No webhook.
* No Twilio.
* No queue.
* No LLM.
* No outbound messaging.

## M8.1B - Postgres runtime model parity

Closed.

### Delivered

* Added SQLAlchemy table models for the current runtime persistence entities:

  * `products`;
  * `customers`;
  * `orders`;
  * `order_items`;
  * `stock_movements`;
  * `parse_log`.
* Added the first Alembic migration:

  * `2026_06_01_1557-aec69eff0019_create_current_runtime_tables.py`.
* Added `PostgresStorage`.
* Implemented product and customer persistence methods.
* Implemented order and order-item persistence methods.
* Implemented stock movement and parse-log persistence methods.
* Added UTC-aware datetime normalization for SQLite-backed test reads.
* Added focused SQLite-backed tests for Postgres storage behavior.
* Added `PostgresStorage` to the shared storage contract fixture.
* Verified the storage contract now runs against:

  * `InMemoryStorage`;
  * `PostgresStorage`;
  * `GoogleSheetsStorage` only when `live_sheets` is enabled.

### Verification

* `pytest tests/test_postgres_models.py -q` -> 8 passed.
* `pytest tests/test_alembic_scaffold.py -q` -> 5 passed.
* `pytest tests/test_postgres_foundation.py -q` -> 4 passed.
* `pytest tests/test_postgres_storage_products_customers.py -q` -> 5 passed.
* `pytest tests/test_postgres_storage_orders.py -q` -> 7 passed.
* `pytest tests/test_postgres_storage_stock_parse.py -q` -> 6 passed.
* `pytest tests/test_storage_contract.py -q` -> 30 passed, 15 deselected.
* SQLite Alembic smoke test passed:

  * `alembic upgrade head`;
  * `alembic downgrade base`;
  * `alembic upgrade head`;
  * `alembic current` -> `aec69eff0019 (head)`.
* `ruff check` passed for the Postgres storage and migration-related files.
* `git diff --check` passed.
* `git status --short` was clean.

### Explicitly not included

* No runtime backend selection.
* No Streamlit wiring to Postgres.
* No live Postgres or Neon connection.
* No demo reseeding into Postgres.
* No FastAPI.
* No webhook.
* No Twilio.
* No queue.
* No LLM.
* No outbound messaging.

## M7.6 - Dashboard demo realism and presentation closure

Closed.

### Delivered

* Expanded demo customers from 30 to 730.
* Rebalanced demo orders into regular, medium-tail, and one-time customers.
* Replaced flat date cycling with deterministic demand-weighted daily volume.
* Added curated signature item weighting and Colombian restaurant pairings.
* Added demo reference-date behavior:

  * demo mode uses the max local order date from loaded orders;
  * runtime mode uses the real current date.
* Fixed Today’s Pulse COP truncation.
* Added today status strip.
* Improved Week Trend readability with split visuals:

  * orders line chart;
  * revenue bar chart.
* Replaced Top items this week with Top items by category.
* Replaced Status breakdown with Week over week.
* Added week-to-date versus prior week-to-date comparison using Monday-start periods.
* Added inverted cancellation-rate color semantics:

  * cancellation down = green down-arrow;
  * cancellation up = red up-arrow.
* Preserved the dashboard read-budget protection at 4 full-sheet reads.
* Kept dashboard service code free of Streamlit imports.

### Final demo dataset

* Customers: 730
* Products: 52
* Orders: 1,500
* Order items: 3,889
* Seed: 42
* Tenant: `el-fogon-colombiano`

### Current dashboard widget set

* Today’s pulse
* Week over week
* Week trend
* Time-of-day heatmap
* Customer mix
* Top customers
* Top items by category
* Items frequently ordered together

### Verification

* Dashboard widget tests and read-budget tests passed.
* `scripts/measure_sheets_reads.py` confirmed the cold-cache dashboard render remains at 4 full-sheet reads.
* Services grep confirmed no Streamlit imports/use in the service layer.
* `git diff --check` passed.
* Manual Streamlit demo verification confirmed all eight widgets render with the demo banner.

## M7.4 - Dashboard polish and M7 closure

### Delivered

- Polished `pages/3_Dashboard.py` with one page title and a concise dashboard caption.
- Grouped dashboard sections into:
  - Now;
  - This week;
  - Patterns.
- Added consistent empty-state messages across dashboard render helpers.
- Standardized dashboard formatting:
  - COP values as `COP 45.000`;
  - counts with thousand-separator dots;
  - percentages with one decimal place.
- Added friendly page-level dashboard load error handling.
- Added `tests/test_dashboard_page.py` for the dashboard load-error path.
- Preserved one `sheets_request_context(storage)` per dashboard render.
- Preserved one `run_locked_dashboard_read_scenario(...)` call per dashboard render.
- Preserved the locked four-tab read union:
  - `orders`;
  - `order_items`;
  - `customers`;
  - `products`.

### Verification

- `python -m compileall src\duna_orders\ui\dashboard_streamlit.py pages\3_Dashboard.py tests\test_dashboard_page.py` -> OK.
- `pytest tests\test_dashboard_page.py tests\test_dashboard_widgets.py tests\test_sheets_read_budget.py -v` -> 33 passed.
- `pytest tests/test_dashboard_widgets.py tests/test_sheets_read_budget.py -v` -> 32 passed.
- `python scripts/measure_sheets_reads.py` -> Pass: True, 4 full-sheet reads.
- `pytest tests/ -v -m "not live_sheets and not live_api"` -> 140 passed, 16 deselected.
- `pytest -m live_sheets -v` with `LIVE_SHEETS_TEST_DELAY_S=10` -> 15 passed, 141 deselected.
- Streamlit smoke check -> dashboard opens and all eight widgets render.
- `git diff --check` -> clean.

### Notes

- M7.4 added polish only.
- No new widgets were added.
- No dashboard scenario change was made.
- No new storage reads were added.
- No `StorageInterface`, `OrderService`, or domain Pydantic model changes were made.
- No new dashboard tabs were accessed.

## M7 - Dashboard page for read-only pilot visibility

Closed.

Completed scope:

- Implemented the full locked eight-widget dashboard:
  - today's pulse;
  - week trend;
  - status breakdown;
  - time-of-day heatmap;
  - customer mix;
  - top customers leaderboard;
  - top items this week;
  - items frequently ordered together.
- Completed M7 in four slices:
  - M7.1: dashboard skeleton and simple aggregation widgets;
  - M7.2: leaderboard widgets;
  - M7.3: analytical widgets;
  - M7.4: polish, verification, and closure docs.
- Kept dashboard computation in `src/duna_orders/services/dashboard.py`.
- Kept Streamlit rendering in `src/duna_orders/ui/dashboard_streamlit.py`.
- Kept the dashboard page in `pages/3_Dashboard.py`.
- Preserved the locked cold-cache read budget of no more than 4 full-sheet reads.
- Preserved the migration-safe service layer for future web app or bot summaries.

Final verification:

- `pytest tests/test_dashboard_widgets.py tests/test_sheets_read_budget.py -v` -> 32 passed.
- `python scripts/measure_sheets_reads.py` -> Pass: True, 4 full-sheet reads.
- `pytest tests/ -v -m "not live_sheets and not live_api"` -> 140 passed, 16 deselected.
- `pytest -m live_sheets -v` with `LIVE_SHEETS_TEST_DELAY_S=10` -> 15 passed, 141 deselected.
- Streamlit smoke check -> dashboard opens and all eight widgets render.
- `git diff --check` -> clean.

## M7.3 - Dashboard analytical widgets

### Delivered

- Added time-of-day heatmap compute objects:
  - `TimeOfDayCell`;
  - `TimeOfDayHeatmapResult`.
- Added product-pair compute objects:
  - `ProductPairEntry`;
  - `ProductPairsResult`.
- Added `compute_time_of_day_heatmap(...)`.
- Added `compute_product_pairs(...)`.
- Added deterministic tests for:
  - heatmap weekday/hour aggregation;
  - Bogotá timezone bucketing;
  - 28-day trailing heatmap window;
  - full 168-cell heatmap grid with zero cells;
  - empty heatmap input behavior;
  - product-pair counting;
  - pair tie-break behavior;
  - duplicate product deduplication inside one order;
  - canonical pair ordering;
  - pair limit and empty input behavior;
  - pair week-window filtering;
  - missing catalog product fallback.
- Added Streamlit render helpers:
  - `render_time_of_day_heatmap(...)`;
  - `render_product_pairs(...)`.
- Wired both analytical widgets into `pages/3_Dashboard.py`.
- Preserved one `sheets_request_context(storage)` per dashboard render.
- Preserved one `run_locked_dashboard_read_scenario(...)` call per dashboard render.
- Preserved the locked four-tab read union:
  - `orders`;
  - `order_items`;
  - `customers`;
  - `products`.

### Verification

- `python -m compileall src\duna_orders\services\dashboard.py src\duna_orders\ui\dashboard_streamlit.py pages\3_Dashboard.py tests\test_dashboard_widgets.py tests\test_sheets_read_budget.py scripts\measure_sheets_reads.py` -> OK.
- `pytest tests\test_dashboard_widgets.py tests\test_sheets_read_budget.py -v` -> 32 passed.
- `python scripts\measure_sheets_reads.py` -> Pass: True, 4 full-sheet reads.
- Streamlit smoke check -> dashboard page opens and renders all eight locked widgets.
- `pytest tests/ -v` -> 139 passed, 16 deselected.
- `pytest -m live_sheets -v` with `LIVE_SHEETS_TEST_DELAY_S=10` -> 15 passed, 140 deselected.
- `git diff --check` -> clean.

### Notes

- M7.3 implements only analytical widgets.
- M7.4 remains for polish, layout, labels, empty states, formatting consistency, and final M7 closure docs.
- No `StorageInterface`, `OrderService`, or domain Pydantic model changes were made.
- No new dashboard tabs were accessed.
- Altair was used only in the Streamlit render layer for the heatmap.
## M7.2 - Dashboard leaderboard widgets

### Delivered

- Added dashboard leaderboard compute objects for:
  - top customers;
  - top items this week.
- Added `compute_top_customers(...)`.
- Added `compute_top_items(...)`.
- Added deterministic tests for:
  - customer leaderboard ranking by spend;
  - customer leaderboard tie-break by customer name;
  - anonymous and unknown customers excluded;
  - customer leaderboard limit behavior;
  - customer leaderboard empty input behavior;
  - customer leaderboard week-window filtering;
  - item leaderboard ranking by quantity;
  - item leaderboard tie-break by product name;
  - missing catalog product fallback;
  - item leaderboard limit behavior;
  - item leaderboard empty input behavior;
  - item leaderboard week-window filtering.
- Added Streamlit render helpers:
  - `render_top_customers(...)`;
  - `render_top_items(...)`.
- Wired the two leaderboard widgets into `pages/3_Dashboard.py`.
- Preserved one `sheets_request_context(storage)` per dashboard render.
- Preserved one `run_locked_dashboard_read_scenario(...)` call per dashboard render.
- Preserved the locked four-tab read union:
  - `orders`;
  - `order_items`;
  - `customers`;
  - `products`.

### Verification

- `python -m compileall src\duna_orders\services\dashboard.py src\duna_orders\ui\dashboard_streamlit.py pages\3_Dashboard.py tests\test_dashboard_widgets.py tests\test_sheets_read_budget.py scripts\measure_sheets_reads.py` -> OK.
- `pytest tests\test_dashboard_widgets.py tests\test_sheets_read_budget.py -v` -> 21 passed.
- `python scripts\measure_sheets_reads.py` -> Pass: True, 4 full-sheet reads.
- Streamlit smoke check -> dashboard page opens and renders M7.1 widgets plus top customers and top items.
- `pytest -m live_sheets -v` with `LIVE_SHEETS_TEST_DELAY_S=10` -> 15 passed, 129 deselected.
- `pytest tests/ -v` -> 128 passed, 16 deselected.
- `git diff --check` -> clean.

### Notes

- M7.2 implements only leaderboard widgets.
- Time-of-day heatmap and item-pair analysis remain deferred to M7.3.
- No `StorageInterface`, `OrderService`, or domain Pydantic model changes were made.
- No new dashboard tabs were accessed.
## M7.1 - Dashboard skeleton and simple aggregation widgets

### Delivered

- Added `src/duna_orders/services/dashboard.py`.
- Added pure dashboard compute functions for:
  - today's pulse;
  - week trend;
  - status breakdown;
  - customer mix.
- Refactored `src/duna_orders/services/dashboard_read_scenario.py` so the locked scenario returns raw typed records through `DashboardScenarioResult`.
- Preserved the locked dashboard tab union:
  - `orders`;
  - `order_items`;
  - `customers`;
  - `products`.
- Added `src/duna_orders/ui/dashboard_streamlit.py` with Streamlit-native render helpers only.
- Added `pages/3_Dashboard.py`.
- Wrapped the dashboard page body in one `sheets_request_context(storage)`.
- Kept widget computation storage-independent.
- Added deterministic tests in `tests/test_dashboard_widgets.py`.
- Updated the read-budget test and measurement script for the raw-record scenario result.

### Verification

- `python -m compileall src\duna_orders\services\dashboard.py src\duna_orders\services\dashboard_read_scenario.py src\duna_orders\ui\dashboard_streamlit.py pages\3_Dashboard.py scripts\measure_sheets_reads.py tests\test_dashboard_widgets.py tests\test_sheets_read_budget.py` -> OK.
- `pytest tests\test_dashboard_widgets.py tests\test_sheets_read_budget.py -v` -> 9 passed.
- `python scripts\measure_sheets_reads.py` -> Pass: True, 4 full-sheet reads.
- Streamlit smoke check -> dashboard page opens and renders the four M7.1 widgets.
- `pytest -m live_sheets -v` with `LIVE_SHEETS_TEST_DELAY_S=10` -> 15 passed, 117 deselected.
- `pytest tests/ -v` -> 116 passed, 16 deselected.
- `git diff --check` -> clean.

### Notes

- M7.1 implements only the first four dashboard widgets.
- Time-of-day heatmap, top customers, top items this week, and item pairs remain deferred to later M7 slices.
- No `StorageInterface`, `OrderService`, or domain Pydantic model changes were made.
## M6.5.4 - Exit verification and documentation

### Delivered

- Locked the dashboard prototype scenario before M7.
- Added `src/duna_orders/services/dashboard_read_scenario.py`.
- Added `scripts/measure_sheets_reads.py`.
- Added `tests/test_sheets_read_budget.py`.
- Defined the dashboard prototype as one future Streamlit page with eight widgets:
  - today's pulse;
  - week trend;
  - status breakdown;
  - time-of-day heatmap;
  - customer mix;
  - top customers leaderboard;
  - top items this week;
  - items frequently ordered together.
- Defined the required tab union:
  - `orders`;
  - `order_items`;
  - `customers`;
  - `products`.
- Verified the cold-cache dashboard prototype read budget:
  - target: ≤4 full-sheet `get_all_records` calls;
  - measured: 4 full-sheet reads;
  - result: pass.
- No dashboard UI was implemented.
- No Streamlit page edits were made.
- No `StorageInterface`, `OrderService`, UI semantic, or Pydantic model changes were made.

### Verification

- `python -m compileall src\duna_orders\services\dashboard_read_scenario.py scripts\measure_sheets_reads.py tests\test_sheets_read_budget.py` -> OK.
- `pytest tests\test_sheets_read_budget.py -v` -> 2 passed.
- `python scripts\measure_sheets_reads.py` -> Pass: True.
- `git diff --check` -> clean.

### Notes

- This closes M6.5 as the Sheets performance / cleanup slice.
- M7 is unlocked only after the external restaurant-owner validation conversation is completed.

## M6.5.3 - Short-TTL Sheets record cache

### Delivered

- Added `src/duna_orders/storage/sheets_cache.py`.
- Added a short-TTL, process-local cache for full-tab Google Sheets records.
- Cache key is `(spreadsheet_id, sheet_name)`.
- Cache is per-`GoogleSheetsStorage` instance, not module-level.
- Tenant filtering remains outside the cache because `get_all_records` loads full tabs.
- Added 30-second TTL with injectable time source for deterministic tests.
- Updated `GoogleSheetsStorage._load_records(...)` to consult the cache.
- Preserved request-scoped precedence: active request-context records are reused before the cache is consulted.
- Added write invalidation for:
  - products on `upsert_product(...)`;
  - customers on `create_customer(...)`;
  - orders and order_items on `create_order(...)`;
  - orders on `update_order_status(...)`;
  - stock_movements on `append_stock_movement(...)`.
- Ensured failed reads are not cached.
- Ensured cache hits return safe record copies.
- Updated request-context tests to account for legitimate cross-request cache reuse.
- Added `tests/test_sheets_cache.py`.

### Verification

- `python -m compileall src\duna_orders\storage\sheets_cache.py src\duna_orders\storage\sheets.py tests\_fakes.py tests\test_sheets_cache.py tests\test_sheets_request_context.py` -> OK.
- `pytest tests\test_sheets_cache.py -v` -> 11 passed.
- `pytest tests\test_sheets_request_context.py -v` -> 6 passed.
- `pytest tests\test_sheets_read_consolidation.py -v` -> 3 passed.
- `git diff --check` -> clean.

### Notes

- No `StorageInterface` changes.
- No `OrderService`, UI semantic, or Pydantic model changes.
- Dashboard read-budget work remains deferred to M6.5.4.

## M6.5.2 - Request-scoped Sheets read consolidation

### Delivered

- Added `src/duna_orders/storage/read_context.py` with an explicit `sheets_request_context(storage)` context manager.
- Implemented request-scoped read reuse through a module-level `ContextVar`.
- Updated `GoogleSheetsStorage` read methods to reuse the active `_SheetsRecordSet` across storage method calls inside one request context.
- Preserved behavior outside any request context: each public read method still creates its own operation-scoped record set.
- Wrapped the read-heavy page body in:
  - `pages/1_New_Order.py`
  - `pages/2_Orders_Today.py`
- Did not use `st.session_state` for request-scoped read reuse.
- Did not change `StorageInterface`, `OrderService`, UI semantics, or Pydantic models.
- Did not introduce cross-request caching.

### Verification

- `python -m compileall src\duna_orders\storage\read_context.py src\duna_orders\storage\sheets.py pages\1_New_Order.py pages\2_Orders_Today.py tests\test_sheets_request_context.py` -> OK.
- `pytest tests\test_sheets_request_context.py -v` -> 6 passed.
- `pytest tests\test_sheets_read_consolidation.py -v` -> 3 passed.
- `git diff --check` -> clean.

### Notes

- Nested Sheets request contexts are intentionally rejected with `RuntimeError`.
- Context teardown resets the `ContextVar`, including exception paths.
- Short-TTL cross-request caching remains deferred to M6.5.3.

## M6.5.1 - Internal Sheets read-provider consolidation

### Delivered

- Centralized Google Sheets full-tab record loading behind the private `GoogleSheetsStorage._load_records(...)` path.
- Added `_SheetsRecordSet` as an operation-scoped record loader inside `GoogleSheetsStorage`.
- Routed read-side hydration through reusable private helpers for:
  - products;
  - customers;
  - orders and order items;
  - stock movements.
- Preserved the public `StorageInterface` contract.
- Did not change OrderService, UI behavior, or Pydantic models.
- Did not add request scoping or cross-request caching; those remain deferred to later M6.5 slices.
- Added fake Sheets test infrastructure with read counters for deterministic read-pattern tests.
- Added `tests/test_sheets_read_consolidation.py`.

### Verification

- `python -m compileall src\duna_orders\storage\sheets.py tests\_fakes.py tests\test_sheets_read_consolidation.py` -> OK.
- `pytest tests\test_sheets_read_consolidation.py -v` -> 3 passed.
- `pytest tests/test_storage_contract.py -v -m "not live_sheets"` -> 15 passed, 15 deselected.
- `git diff --check` -> clean.

### Notes

- M6.5.1 only centralizes internal read loading and creates reusable fake read-count infrastructure.
- Request-scoped consolidation remains deferred to M6.5.2.
- Short-TTL cross-request caching remains deferred to M6.5.3.

## M6 - Customer registry and repeat recognition

### Delivered

- Added customer auto-recognition during draft creation.
- Added phone normalization through `normalize_customer_phone(...)`.
- Phone normalization currently:
  - strips leading/trailing whitespace;
  - removes spaces;
  - removes dashes;
  - does not perform deep international phone normalization.
- When `OrderService.create_draft(...)` receives a customer phone:
  - it looks up an existing customer by `(tenant_id, normalized_phone)`;
  - if found, it associates the order with the existing `customer_id`;
  - if not found, it creates a new customer;
  - registered customer name takes precedence over the newly typed name.
- Added `StorageInterface.get_customer_order_history(...)`.
- Implemented customer order history in both `InMemoryStorage` and `GoogleSheetsStorage`.
- Added `src/duna_orders/services/customer_context.py` for shared customer context and repeat-customer labels.
- Added customer context to the New Order page:
  - `Cliente nuevo`;
  - `Cliente conocido: [name] - [N] pedido(s) anterior(es)`;
  - note when a typed name differs from the registered customer name.
- Added customer badges to Today’s Orders:
  - `First order`;
  - `Repeat customer (N orders)`.
- Added deterministic Colombian-Spanish WhatsApp confirmation message generation in `src/duna_orders/ui/confirmation_message.py`.
- Added WhatsApp confirmation message display after order confirmation.
- Updated parser-created draft flow so customer name/phone fields are reused instead of hardcoded anonymous customer data.
- Added Sheets deserialization safeguards for numeric-looking phone values in `customer_phone` and `customer_phone_snapshot`.
- Added retry repair logic for partial confirmation cases where a deterministic sale stock movement already exists but the order status is still `draft`.

### Verification

- `python -m compileall src tests scripts pages streamlit_app.py` -> OK.
- `pytest tests/ -v -m "not live_sheets and not live_api"` -> 86 passed, 16 deselected.
- `pytest -m live_sheets -v` with `LIVE_SHEETS_TEST_DELAY_S=12` -> 15 passed, 87 deselected.
- Manual Sheets-backed Streamlit verification passed:
  - new customer phone created a customer row in Google Sheets;
  - repeated phone recognized the stored customer;
  - typed name mismatch kept the registered customer name;
  - parser-created drafts used the same customer name/phone fields;
  - WhatsApp confirmation message displayed after confirmation;
  - Today’s Orders showed customer context;
  - inconsistent draft-plus-stock-movement runtime order was repaired successfully.

### Notes

- No parser prompts were changed.
- No outbound WhatsApp messaging was added.
- No customer profile editing UI was added.
- No dashboard analytics were added.
- No multi-phone customer support was added.
- `GoogleSheetsStorage.get_customer_order_history(...)` is currently naive: it calls `list_orders()`, hydrates the full order list, and filters by `tenant_id` and `customer_id` in Python.
- Customer order history read optimization is deferred into M6.5 as part of the Sheets performance / cleanup slice.
- Google Sheets 429 quota/read pressure remains a known optimization item before dashboard work.
- `OrderService.confirm_order(...)` repairs partial-confirmation retries only when the existing sale stock movement exactly matches the expected deterministic payload.

## M5 - Order lifecycle and today's-orders visibility

### Delivered

- Extended order statuses from creation/confirmation into a simple operational lifecycle:
  - `draft`
  - `confirmed`
  - `in_preparation`
  - `ready`
  - `delivered`
  - `picked_up`
  - `cancelled`
- Added `status_updated_at` to orders as the latest lifecycle timestamp.
- Added `OrderService.transition_order_status(...)` for controlled lifecycle transitions.
- Added service-level transition validation:
  - `confirmed` -> `in_preparation`, `cancelled`
  - `in_preparation` -> `ready`, `cancelled`
  - `ready` -> `delivered`, `cancelled` for delivery orders
  - `ready` -> `picked_up`, `cancelled` for pickup orders
  - terminal states cannot transition further.
- Added tenant scoping to lifecycle transitions.
- Extended `StorageInterface.update_order_status(...)` and both storage backends to persist `status_updated_at`.
- Added `src/duna_orders/services/order_visibility.py` for testable today/order visibility filtering.
- Added `pages/2_Orders_Today.py` for active order visibility and lifecycle actions.
- Replaced the empty dashboard placeholder page with the Today’s Orders page.
- Updated live Sheets test setup so it can read `GOOGLE_SHEETS_TEST_SPREADSHEET_ID` from project settings.
- Updated `.env.example` so live test Sheet ID is blank by default and not copied from the runtime Sheet ID.
- Documented the `status_updated_at` Sheets migration in `MIGRATIONS.md`.

### Verification

- `python -m compileall src tests scripts pages streamlit_app.py` -> OK.
- `pytest tests/ -v -m "not live_sheets and not live_api"` -> 74 passed, 14 deselected.
- `pytest -m live_sheets -v` -> 13 skipped, 71 deselected because `GOOGLE_SHEETS_TEST_SPREADSHEET_ID` is intentionally blank until a separate live-test spreadsheet is created.
- Manual Sheets-backed Streamlit check passed after refreshing through a transient Google Sheets 429 quota error:
  - existing confirmed orders appeared in Today’s Orders;
  - lifecycle actions worked through preparation, ready, and delivered states;
  - completed/cancelled toggle worked.

### Notes

- No parser prompts were changed.
- No customer registry was added.
- No dashboard analytics were added.
- No status history table or audit log was added; `status_updated_at` is the current lightweight lifecycle timestamp.
- A separate live-test Google Sheet remains deferred.
- Google Sheets quota/read optimization remains a future cleanup item.
## M4.3 - Streamlit Sheets backend wiring

### Delivered

- Added `DUNA_STORAGE_BACKEND` setting with `memory` as the default backend.
- Added `GOOGLE_SHEETS_SPREADSHEET_ID` to runtime settings.
- Updated `get_storage()` so Streamlit can use either `InMemoryStorage` or `GoogleSheetsStorage`.
- Kept memory backend behavior unchanged for local demo use.
- Added fail-fast startup behavior when `DUNA_STORAGE_BACKEND=sheets` is selected without required Sheets configuration.
- Added `prepare_storage_catalog(...)` so memory storage is seeded from the demo catalog, while Sheets storage only checks whether products already exist.
- Avoided automatic product upserts on every Streamlit startup when using Sheets.
- Updated `scripts/seed_demo_catalog.py` to read the runtime Sheet ID and credentials through project settings.
- Renamed the Streamlit reset button to `Reset UI session` to clarify that it clears local UI state only.
- Fixed duplicate-product stock impact during order confirmation by aggregating quantities by `product_id`.
- Added regression coverage for duplicate product lines and aggregate insufficient-stock checks.

### Verification

- `python -m compileall scripts\seed_demo_catalog.py src\duna_orders\config.py` -> OK.
- `python scripts/seed_demo_catalog.py --dry-run` -> 52 products loaded from catalog.
- `pytest tests/test_seed_demo_catalog.py tests/test_ui_setup.py tests/test_orders_service.py -v` -> 31 passed.
- `pytest tests/ -v` -> 63 passed, 13 deselected.
- Manual memory backend check passed:
  - backend displayed as `InMemoryStorage`;
  - products loaded;
  - draft creation worked;
  - confirmation worked;
  - inventory decreased;
  - reset reseeded local memory state.
- Manual Sheets backend check passed:
  - backend displayed as `GoogleSheetsStorage`;
  - products loaded from Sheets;
  - parser-assisted draft worked;
  - confirmation worked;
  - order row appeared in `orders`;
  - item rows appeared in `order_items`;
  - stock movements appeared in `stock_movements`;
  - parse log appeared in `parse_log`;
  - restart plus `get_order(...)` verified persistence.
- Duplicate product stock impact verified manually:
  - two Bandeja paisa order item lines produced one aggregated stock movement with `quantity_delta = -2`;
  - one aguacate item produced `quantity_delta = -1`.

### Notes

- `scripts/seed_demo_catalog.py --delay-s 8` is now treated as a one-time setup/catalog-refresh command, not a normal startup command.
- Runtime Streamlit configuration is read from `.env`; `.env.example` is only the template.
- `Reset UI session` clears Streamlit session state only. It does not reset persistent Google Sheets inventory.
- No new retry/backoff infrastructure was added in M4.3.
- No order lifecycle changes, customer registry, dashboard, prompt changes, new pages, or new domain fields were added.
- M4.3 closes the persistence gap for the operator-facing demo.


## M4.2.6b - Parser-assisted draft creation

### Delivered

- Added realistic demo WhatsApp messages in `data/demo_messages.json`.
- Added `src/duna_orders/demo_messages.py` for loading and validating demo messages.
- Added parser review UI support in `src/duna_orders/ui/parser_review.py`.
- Added `DraftCandidate` and `DraftCandidateItem` review models.
- Added `parsed_result_to_draft_candidate(...)` to convert parser output into reviewable draft candidates.
- Integrated parser-assisted draft creation into `pages/1_New_Order.py`.
- Added a demo message selector and parser button to the New Order page.
- Added cached parser calls keyed by message text and `PROMPT_VERSION`.
- Added operator review before draft creation.
- Kept manual product picker and manual draft creation unchanged.
- Fixed parser availability in Streamlit by reading `settings.anthropic_api_key` instead of direct `os.getenv("ANTHROPIC_API_KEY")`.
- Updated the Anthropic prompt so live parser output includes `tenant_id`.
- Bumped `PROMPT_VERSION`.
- Added parser payload normalization for common LLM quirks:
  - mixed-case payment methods;
  - fulfillment aliases;
  - leading/trailing whitespace;
  - empty optional string fields;
  - item product/modification whitespace cleanup.
- Preserved `customer_name=""` because `DraftOrderRequest.customer_name` is currently required as a string.

### Verification

- `python -m compileall src tests scripts pages streamlit_app.py` -> OK.
- `pytest tests/ -v` -> 54 passed, 13 deselected.
- `pytest -m live_api -v` -> 1 passed, 66 deselected.
- `pytest -m live_sheets -v` -> reached Google Sheets; latest run got 9 passed, 3 failed, 55 deselected due external Google Sheets API 429 read quota.
- Manual Streamlit check without API key passed:
  - demo selector populated message;
  - parser warning displayed;
  - manual draft creation worked;
  - manual confirmation worked;
  - inventory decreased.
- Manual Streamlit check with API key passed for `msg_002_modifications_combined`:
  - parser panel rendered;
  - quantity edit worked;
  - draft creation from parser worked;
  - order confirmation worked;
  - inventory decreased correctly.
- Manual Streamlit check with API key passed for `msg_016_informal_messy`:
  - parser panel rendered;
  - draft creation from parser worked;
  - order confirmation worked;
  - inventory decreased correctly.

### Notes

- Live Sheets failures during final close were caused by Google Sheets API 429 read quota, not assertion failures or parser-assisted draft regressions.
- The parser interpretation of “dos bandejas paisas, una sin chicharrón y la otra con extra aguacate” as two Bandeja paisa items plus a separate Porción de aguacate is acceptable for order management because it improves pricing and stock impact accuracy.
- For messy informal messages, the parser produced a usable draft, but address/location text may still land in inferred notes instead of a dedicated delivery field.
- `customer_name=""` and `packaging_fee=0` remain acceptable for M4.2.6b and are tracked as follow-up work.
- `pages/1_New_Order.py` remains a single page for now. Composition/page extraction is deferred until the documented split triggers are reached.
- M4.2.6b is now closed.
- M4.2 is now closed.

## M4.2.6a — UI factory extraction

### Changed

- Added `src/duna_orders/ui/setup.py` for UI composition factories.
- Added `get_storage()` for current UI storage backend construction.
- Added `get_order_service(storage)` for `OrderService` wiring.
- Added `get_parsing_service(storage)` for optional parser-service wiring when `ANTHROPIC_API_KEY` is set.
- Added cached `get_demo_catalog()` for validated demo catalog loading.
- Added `seed_inmemory_from_catalog(storage, catalog)` for idempotent in-memory catalog seeding.
- Refactored `pages/1_New_Order.py` to use UI setup factories instead of inline setup logic.

### Verification

- `python -m compileall src tests scripts pages streamlit_app.py` → OK.
- `pytest tests/ -v` → 42 passed, 13 deselected.
- `pytest -m live_sheets -v` → 12 passed, 43 deselected.
- Manual Streamlit check passed: New Order page renders, catalog loads, draft creates, order confirms, and inventory decreases.

### Notes

- No new UI behavior was added.
- No parser-assisted draft creation was added.
- No storage backend switch was implemented.
- M4.2.6b remains the next slice.

## M4.2.5b — Tenant foundation closed

### Delivered

- Added required `tenant_id` to tenant-scoped domain and request models.
- Propagated `tenant_id` through `OrderService` and `ParsingService`.
- Kept tenant selection outside the parser; the parser does not infer tenant identity from customer message text.
- Added catalog-level business metadata using a top-level `business` block.
- Kept catalog products tenant-less in the JSON file and injected `business.tenant_id` when loading products.
- Updated Google Sheets headers for `products`, `customers`, `orders`, `order_items`, `stock_movements`, and `parse_log`.
- Placed `tenant_id` as column B / position 2 on all six tenant-scoped tabs.
- Updated `GoogleSheetsStorage` serialization and deserialization for tenant-aware entities.
- Updated the Google Sheets smoke script to construct tenant-aware entities.
- Manually migrated the live test spreadsheet headers.
- Seeded the demo catalog into the migrated live test spreadsheet.

### Verification

- `python -m compileall src tests scripts pages streamlit_app.py` → OK.
- `pytest tests/ -v` → 36 passed, 13 deselected.
- `pytest -m live_sheets -v` → 12 passed, 37 deselected.
- `python scripts/seed_demo_catalog.py --delay-s 8` → 52 products upserted.
- `python scripts/smoke_google_sheets.py` → All smoke checks passed.

### Notes

- Initial demo catalog seeding with `--delay-s 2` hit Google Sheets APIError 429 read quota.
- No retry/backoff infrastructure was added in M4.2.5b-E because it was out of scope.
- Rerunning the idempotent seed script with `--delay-s 8` succeeded.
- M4.2.5b is now closed.
- Next milestone: M4.2.6 parser-assisted draft creation.

## M4.2.5b-D — Google Sheets tenant schema preparation

### Changed

- Added `tenant_id` to Google Sheets schema headers for `products`, `customers`, `orders`, `order_items`, `stock_movements`, and `parse_log`.
- Placed `tenant_id` as the second column on every tab, immediately after the primary ID column.
- Updated `GoogleSheetsStorage` serialization and deserialization for tenant-aware entities.
- Updated the Google Sheets smoke script to construct tenant-aware entities using `el-fogon-colombiano`.

### Migration

- Documented the manual Google Sheets header migration in `MIGRATIONS.md`.
- Documented the expected D/E transition state where bootstrap validation rejects spreadsheets without `tenant_id` columns.
- No automated migration tooling was added.

### Known transition state

- `pytest -m live_sheets -v` remains expected to fail until M4.2.5b-E because the live test spreadsheet has not been manually migrated yet.

### Verified

- `python -m compileall src tests scripts pages streamlit_app.py` → OK.
- `pytest tests/ -v` → 36 passed, 13 deselected.

### Changed
- M4.2.5b-B: added required `tenant_id` to tenant-scoped domain/request models and propagated it through `OrderService` and `ParsingService`.
- Added shared `DEFAULT_TEST_TENANT_ID` for deterministic tests.
- Updated order, storage-contract, parsing-service, and parser fake tests for tenant-aware in-memory behavior.

### Added
- Added `ARCHITECTURE.md` documenting the customer/operator/owner product vision, tenant identity decision, current architecture, deferred work, and Phase 5 open questions.

### Known transition state
- Live Google Sheets tests are expected to fail until M4.2.5b-D/E because the Sheets schema does not yet include the required `tenant_id` columns.

### Verified
- `python -m compileall src tests scripts pages streamlit_app.py`
- `pytest tests/test_orders_service.py tests/test_storage_contract.py tests/test_parsing_service.py -v` → 30 passed, 12 deselected.

## M4.1 — Google Sheets storage resilience
### Delivered

- Added a central `_run_gspread(...)` execution boundary in `GoogleSheetsStorage`.
- Added retry handling for transient Google Sheets API failures:
  - HTTP 429 quota errors
  - HTTP 5xx server errors
- Kept non-transient errors non-retryable:
  - storage configuration errors
  - authentication errors
  - schema/header mismatches
  - duplicate-ID contract errors
  - missing-ID contract errors
- Routed Sheets reads, writes, updates, worksheet lookups, and bootstrap API calls through the retry boundary.
- Migrated `worksheet.update(...)` calls to the current `gspread` argument order.

### Added
- M4.2 Step 1: extended the demo order flow domain model with Colombian restaurant fields for fulfillment, payment, delivery zone, packaging fee, customer notes, product availability days, and item modifications.
- Added Google Sheets schema support and serialization/deserialization for the new product, order, and order item fields.
- Documented the required M4.2 Google Sheets header migration in `MIGRATIONS.md`.

### Changed
- Updated `OrderService.create_draft` to carry item modifications and fulfillment/payment metadata into draft orders.
- Updated order total calculation to include `packaging_fee` in addition to subtotal and delivery fee.
- Updated storage contract tests, order service tests, and Google Sheets smoke checks for the new fields.

### Verified
- `python -m compileall src tests scripts`
- `pytest tests/ -v` → 30 passed, 13 deselected.
- Manual test spreadsheet header migration completed.
- `pytest -m live_sheets -v` → 12 passed, 33 deselected.
- `python scripts/smoke_google_sheets.py` → All smoke checks passed.

### Verified

- `python -m compileall src tests scripts` → OK.
- `pytest tests/ -v` → passed.
- `pytest -m live_sheets -v` → passed.
- `python scripts/smoke_google_sheets.py` → passed.

### Notes

Retry/backoff improves resilience against transient Google API failures, but it does not turn Google Sheets into a transactional backend. Database-backed storage remains the long-term migration path through `StorageInterface`.

## M3.1 — Parse log prompt versioning

### Delivered

- Added `PROMPT_VERSION` in `src/duna_orders/parsing/prompts.py`.
- Added `prompt_version` to `ParseLogEntry`.
- Added `prompt_version` to the `parse_log` sheet schema.
- Updated `ParsingService` to persist the prompt version on both successful and failed parse attempts.
- Updated `GoogleSheetsStorage` parse log serialization for the new field.
- Updated tests and smoke script constructors for `ParseLogEntry`.

### Verified

- `python -m compileall src tests scripts` → OK.
- `pytest tests/ -v` → `30 passed, 13 deselected`.
- Live Google Sheets validation passed after manually migrating the test spreadsheet `parse_log` header.
- Google Sheets smoke script passed after the header migration.

### Notes

`prompt_version` is now part of parser auditability. Any future prompt change should update `PROMPT_VERSION` so parse logs can be tied back to the exact prompt version that produced them.

## M3 — Storage contract and live Google Sheets validation

### Delivered

- Implemented `GoogleSheetsStorage` as a concrete `StorageInterface` backend.
- Added Google Sheets persistence for:
  - products
  - customers
  - orders
  - order_items
  - stock_movements
  - parse_log
- Refactored storage tests into `tests/test_storage_contract.py`.
- Added parametrized storage contract tests:
  - memory backend runs by default
  - Google Sheets backend runs with the `live_sheets` marker
- Added hard separation between:
  - `GOOGLE_SHEETS_SPREADSHEET_ID` for runtime / production use
  - `GOOGLE_SHEETS_TEST_SPREADSHEET_ID` for live tests
- Added `run_token`-based isolation for live Sheets tests.
- Added session-scoped `GoogleSheetsStorage` for live tests to reduce setup overhead.
- Added session-end cleanup for rows created by live tests.
- Added `scripts/smoke_google_sheets.py` for end-to-end Sheets validation.

### Verified

- Default test suite:
  - `pytest tests/ -v`
  - result: `30 passed, 13 deselected`
- Live Google Sheets contract suite:
  - `pytest -m live_sheets -v`
  - result: `12 passed, 31 deselected`
- Google Sheets smoke script:
  - `python scripts/smoke_google_sheets.py`
  - passed twice consecutively

### Storage behavior confirmed under live Sheets

- `Product` duplicate ID uses upsert replacement.
- `Customer` duplicate ID raises `ValueError`.
- `Order` duplicate ID raises `ValueError`.
- `StockMovement` duplicate ID raises `ValueError`.
- `ParseLogEntry` duplicate `parse_id` raises `ValueError`.
- `update_order_status` with an unknown `order_id` raises `KeyError`.
- `get_product`, `get_customer`, and `get_order` return `None` for unknown IDs.
- `confirmed_at` persists correctly.
- Datetime round-trip preserved microsecond precision.
- Boolean round-trip passed through `Product.active`.
- `list_orders` works against Google Sheets.
- `list_stock_movements(product_id=...)` works against Google Sheets.

### Google Sheets quota finding

Initial live Sheets runs hit Google Sheets API 429 read quota errors.

Resolution for test workflows:

- Added session-scoped Sheets storage in the fixture.
- Added configurable delay through `LIVE_SHEETS_TEST_DELAY_S`.
- Final successful live test run used `LIVE_SHEETS_TEST_DELAY_S=8`.

No retry/backoff was added to production storage code.

### Known deferrals

- `GoogleSheetsStorage` has no retry/backoff layer for 429 or 5xx errors.
- `gspread` emits deprecation warnings for `worksheet.update(...)` argument order in:
  - `upsert_product`
  - `update_order_status`
- Live Sheets cleanup runs at session end only; crashed test runs may leave orphaned `test_run_*` rows.
- `parse_log` does not yet include `prompt_version`.

### Notes

Google Sheets is the current persistence backend for pilot validation, not the core architecture. `StorageInterface` remains the migration boundary for future database backends.

### Added
- M4.2.3: added the full 52-item demo restaurant catalog for `El Fogón Colombiano`.
- Added `DemoCatalogFile` validation and `load_demo_catalog()` for fail-fast demo catalog loading.
- Added deterministic catalog tests for product count, category distribution, restricted weekday availability, and parrilla weight variants.

### Verified
- `python -m compileall src tests scripts`
- `pytest tests/ -v` → 33 passed, 13 deselected.

### Added
- M4.2.4: added a products-only idempotent demo catalog seed script for Google Sheets.
- Added deterministic seed helper tests covering full catalog upsert behavior and dry-run behavior.
- Added configurable per-product delay for safer Google Sheets seeding under API quota limits.

### Verified
- `python -m compileall src tests scripts`
- `pytest tests/ -v` → 35 passed, 13 deselected.
- `python scripts/seed_demo_catalog.py --dry-run` → 52 products loaded.
- `python scripts/seed_demo_catalog.py --delay-s 2` → 52 products upserted.
- `pytest -m live_sheets -v` → 12 live tests passed, but teardown cleanup failed with Google Sheets API 429 read quota. Manual cleanup may be needed for temporary `test_run_...` rows.
