# M9.4B Conversation Observability/Read-Model Design

Status: design only.

Baseline: `efaaf82 docs(m9): close conversation hardening tests`

M9.4B addresses the first of the two remaining M9.4 scope items: observability
hooks for a later operator conversation view. It answers two questions:

* What is already observable today, with no schema change?
* What is genuinely missing, and what would it take to add it?

M9.4B is docs/design only. M9.4C and M9.4D were implemented later (see
sections 11 and 12).

```text
draft -> approved -> confirmed -> atomic inventory commit -> outbound acknowledgement
```

The downstream lifecycle above is unaffected by anything in this document.

## 1. Pre-flight findings

### 1.1 Existing observability available today

`PostgresConversationStateStore` and `PostgresConversationOrderLookup` already
expose, via `get_session(...)`, `get_latest_session_for_customer(...)`,
`list_turns(...)`, and `get_order_by_conversation_id(...)`:

* `conversation_id` - `ConversationSession.conversation_id` (primary key).
* `tenant_id` - `ConversationSession.tenant_id`.
* `customer_phone` - `ConversationSession.customer_phone`.
* `status` - `ConversationSession.status`
  (`open`, `draft_created`, `expired`, `failed`; only `open` and
  `draft_created` are written by any code today).
* `opened_at` - `ConversationSession.opened_at`.
* `created_at` - `ConversationSession.created_at`.
* `last_message_at` - `ConversationSession.last_message_at` (bumped on every
  appended turn).
* `updated_at` - `ConversationSession.updated_at` (bumped on turn append and
  on `mark_draft_created`).
* `version` - `ConversationSession.version` (optimistic-concurrency counter).
* ordered turns with `message_sid` / `body` / `received_at` /
  `sequence_number` - `ConversationStateStore.list_turns(tenant_id=...,
  conversation_id=...)`, ordered by `sequence_number`.
* `resulting_order_id` - `ConversationSession.resulting_order_id` (set by
  `mark_draft_created(...)`).
* order lookup - `ConversationOrderLookup.get_order_by_conversation_id(
  tenant_id=..., conversation_id=...)` returns the full `Order` (with items)
  when a draft exists.

This is essentially the complete observability hook list from
`docs/M9_CONVERSATION_STATE_ARCHITECTURE.md` section 9, with one exception:
*"latest parse status or safe parse error classification"* is not persisted
anywhere. `ConversationAdvancementResult.outcome` is computed on every
`advance(...)` call but is returned to the caller and discarded; it is never
written back onto `conversation_sessions`.

### 1.2 Existing precedent: `InboundDraftReviewService`

`src/duna_orders/services/inbound_draft_review.py` already implements the
shape a conversation read-model should follow:

* `InboundDraftReviewItem` / `InboundReviewDiagnostics` /
  `InboundReviewSnapshot` are plain `@dataclass(frozen=True)` DTOs.
* `InboundDraftReviewService.get_inbound_review_snapshot(tenant_id=...)`
  assembles the snapshot from `processed_messages` plus
  `DiagnosticReadService.get_order_for_diagnostics(...)`.
* `pages/5_Inbound_Review.py` consumes the snapshot for an operator-only page.

M9.4C should follow the same shape: tenant-scoped snapshot + diagnostics
dataclasses, assembled by a narrow read-model class, consumed by a future
operator page (no page is built in M9.4B or M9.4C).

### 1.3 Architecture guard precedent

`tests/test_architecture_boundaries.py` enforces `FORBIDDEN_BROAD_READS`
(`get_order`, `list_orders`, `list_customers`, `list_products`,
`list_stock_movements`, `unscoped_list_*`) only against calls on
`storage` / `self._storage` / `st.session_state.storage` receivers inside
`ENFORCED_RUNTIME_READ_MODULES`.

Neither `ENFORCED_RUNTIME_READ_MODULES` nor
`KNOWN_STAGE1_RUNTIME_READ_MODULES` includes `pages/5_Inbound_Review.py`,
`services/inbound_draft_review.py`, `services/diagnostic_reads.py`, or
`storage/conversation_orders.py` (`ConversationOrderLookup`). All four are
either operator-only or narrow Postgres-backed lookups built on direct
SQLAlchemy `select(...)` against `session_scope(...)`, not on
`storage.list_*`/`storage.get_*` calls - so they do not trigger
`FORBIDDEN_BROAD_READS` regardless of guard membership.

`services/conversation_advancement.py` and `web/app.py` *are* in
`ENFORCED_RUNTIME_READ_MODULES` because they sit on the customer-facing
webhook path and must use `TenantScopedReadService.list_products(...)`.

## 2. Central decision: split M9.4 observability work into M9.4C and M9.4D

Decision: split the remaining "observability/read-model" scope into two
independently implementable slices.

* **M9.4C** - a read-only conversation observation/read-model helper that
  uses *only* fields that exist today (section 1.1). No schema change, no
  migration, no change to `ConversationAdvancementService` or any enforced
  module.
* **M9.4D** - persisted observability hooks (`latest_advancement_outcome`,
  `latest_parse_error_category`, possibly `latest_parse_status`) that close
  the one real gap identified in section 1.1. This requires a migration, a
  new `ConversationStateStore` write method, and a small change inside
  `ConversationAdvancementService.advance(...)` (an enforced module).

Rationale:

* M9.4C is strictly additive, has zero migration risk, and can ship
  independently as soon as it is designed.
* M9.4D touches an enforced, customer-facing runtime module and requires a
  schema change and a safety policy for error text (section 4.2). It
  benefits from M9.4C's snapshot shape already existing as a consumer/target,
  and from being reviewed as its own slice given the larger blast radius.
* Splitting avoids blocking the "no schema change" win behind the harder
  schema-and-safety-policy questions.

## 3. M9.4C - Read-only conversation observation/read-model (no schema change)

Status: design only in M9.4B. Implementation deferred to M9.4C.

### 3.1 Boundary

`ConversationObservationReads` is a narrow, Postgres-backed, read-only
protocol living beside `ConversationOrderLookup` in
`src/duna_orders/storage/`, outside `StorageInterface` - the same precedent
M9.2A established for `ConversationOrderLookup`.

It is an **operator/diagnostic read-model**, not part of the customer-facing
webhook runtime. It is consumed by a future operator-only page (not built in
M9.4B/M9.4C), analogous to how `InboundDraftReviewService` backs
`pages/5_Inbound_Review.py`.

### 3.2 Protocol and implementation naming

```python
class ConversationObservationReads(Protocol):
    def get_conversation_observation_snapshot(
        self,
        *,
        tenant_id: str,
        now: datetime,
        idle_threshold: timedelta = timedelta(hours=4),
    ) -> ConversationObservationSnapshot:
        ...


class PostgresConversationObservationReads:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory
```

`now` and `idle_threshold` are explicit parameters so the read-model stays a
pure function of (stored data, now, threshold). The default
`idle_threshold=timedelta(hours=4)` mirrors the "Recommended idle boundary for
first implementation" already documented in
`docs/M9_CONVERSATION_STATE_ARCHITECTURE.md` section 2, used here **only for
display/visibility** - it is not a session-boundary policy decision (see
section 5).

### 3.3 Snapshot-style DTOs

```python
@dataclass(frozen=True)
class ConversationObservationItem:
    conversation_id: str
    tenant_id: str
    customer_phone: str
    status: ConversationSessionStatus
    opened_at: datetime
    last_message_at: datetime
    updated_at: datetime
    version: int
    turn_count: int
    latest_message_sid: str | None
    latest_body_preview: str | None
    resulting_order_id: str | None
    has_draft: bool
    is_idle: bool
    needs_operator_attention: bool


@dataclass(frozen=True)
class ConversationObservationDiagnostics:
    total_count: int
    open_count: int
    draft_created_count: int
    idle_count: int
    needs_attention_count: int


@dataclass(frozen=True)
class ConversationObservationSnapshot:
    items: list[ConversationObservationItem]
    diagnostics: ConversationObservationDiagnostics
```

This mirrors `InboundReviewSnapshot` / `InboundReviewDiagnostics` /
`InboundDraftReviewItem`.

### 3.4 Read-time derived fields

All of the following are computed at read time from existing tables; none
require a new column:

* `turn_count` - `COUNT(*)` over `conversation_turns` for the
  `conversation_id`, or an aggregate join in the snapshot query. No new
  counter column on `conversation_sessions` is introduced by M9.4C.
* `latest_message_sid` - the `message_sid` of the turn with the highest
  `sequence_number` for the conversation.
* `latest_body_preview` - the first ~160 characters of that same turn's
  `body`, with no redaction. `pages/5_Inbound_Review.py` already displays raw
  inbound bodies to operators (`st.code(item.raw_inbound_body, ...)`), so
  showing a preview of conversation turn text follows existing precedent. The
  exact preview length is an implementation detail for M9.4C, not a design
  commitment here.
* `has_draft` / `linked_order_id` - `resulting_order_id is not None` /
  `resulting_order_id`. M9.4C returns the order id only, **not** the full
  `Order`, to avoid an N+1 `ConversationOrderLookup` call per conversation in
  a tenant-wide snapshot. A future operator page can call
  `ConversationOrderLookup.get_order_by_conversation_id(...)` for a single
  conversation on drill-down.
* `is_idle` - `now - last_message_at > idle_threshold`. Pure read-time
  comparison; no new column.
* `needs_operator_attention` - a read-time heuristic only, for example:

  ```text
  needs_operator_attention =
      status == "open"
      and resulting_order_id is None
      and (turn_count >= ATTENTION_TURN_THRESHOLD or is_idle)
  ```

  `ATTENTION_TURN_THRESHOLD` is a tunable constant to be chosen during M9.4C
  implementation (for example 3). This heuristic uses `is_idle` as one input
  but does not require idle-boundary *policy* (section 5) to be designed
  first - it is purely advisory, for an operator list view.

### 3.5 Tenant scoping

`get_conversation_observation_snapshot(...)` requires `tenant_id` and filters
`conversation_sessions` (and the joined `conversation_turns`) by
`tenant_id`, exactly as `PostgresConversationStateStore` and
`PostgresConversationOrderLookup` already do.

### 3.6 Architecture guard membership

Per section 1.3, `PostgresConversationObservationReads` should **not** be
added to `ENFORCED_RUNTIME_READ_MODULES` or
`KNOWN_STAGE1_RUNTIME_READ_MODULES`. It is a narrow Postgres-backed read-model
using direct `select(...)` queries (like `ConversationOrderLookup`), not
`storage.list_*`/`storage.get_*` calls, and it is operator-only - consistent
with `inbound_draft_review.py` and `diagnostic_reads.py` today. If a future
operator page is added, that page should also follow the
`pages/5_Inbound_Review.py` precedent of non-membership in either guard set.

### 3.7 Explicitly excluded from M9.4C

* No new columns on `conversation_sessions` or `conversation_turns`.
* No migration.
* No change to `ConversationStateStore`, `ConversationAdvancementService`, or
  `web/app.py`.
* No UI / operator page.
* `latest_parse_status`, `latest_advancement_outcome`,
  `latest_parse_error_category` (these are M9.4D).

## 4. M9.4D - Persisted observability hooks (schema + service wiring)

Status: design only in M9.4B. Implementation deferred to M9.4D.

### 4.1 Candidate columns on `conversation_sessions`

* `latest_advancement_outcome` - nullable string, mirroring
  `ConversationAdvancementOutcome` (`TURN_APPENDED_INCOMPLETE`,
  `PARSE_INCOMPLETE`, `DRAFT_CREATED`, `ALREADY_HAS_DRAFT`,
  `DUPLICATE_MESSAGE`).
* `latest_parse_error_category` - nullable string, a coarse safe category
  (for example `PARSER_ERROR`), set when `advance(...)` catches a
  `ParserError`.
* `latest_parse_status` - **only if distinct from
  `latest_advancement_outcome`**. `PARSE_INCOMPLETE` and
  `TURN_APPENDED_INCOMPLETE` already function as parse-status signals within
  `latest_advancement_outcome`. M9.4D must decide whether a separate
  finer-grained parse-status field (for example distinguishing "parser
  raised" vs "parser returned an incomplete result" vs "parser succeeded but
  referenced an unknown product") adds enough operator value to justify a
  second column, or whether `latest_advancement_outcome` +
  `latest_parse_error_category` is sufficient. This document does not decide
  that question.

### 4.2 Safety policy for parse-error fields

* Never persist raw parser/LLM exception text or raw parser output.
* `latest_parse_error_category` stores only a small, fixed, safe enum of
  categories chosen by M9.4D (for example `PARSER_ERROR`).
* This policy must be resolved and written into the M9.4D design before any
  implementation, because raw `ParserError` text may echo customer-submitted
  content.

### 4.3 Candidate store method

```python
def record_advancement_attempt(
    self,
    *,
    tenant_id: str,
    conversation_id: str,
    outcome: str,
    parse_error_category: str | None = None,
) -> ConversationSession:
    ...
```

Open question for M9.4D: should `record_advancement_attempt(...)` be called
for the `DUPLICATE_MESSAGE` outcome? `docs/M9_2A_CONVERSATION_ADVANCEMENT_SERVICE_DESIGN.md`
section 6 requires that a duplicate `message_sid` causes "no second turn, no
parser call, no draft creation" - i.e. no session mutation. M9.4D should
decide whether updating `latest_advancement_outcome` on a duplicate would
violate that "no session mutation" guarantee (likely: it would, and
`record_advancement_attempt(...)` should be skipped for
`DUPLICATE_MESSAGE`).

### 4.4 Locking/version discipline

Any write from `record_advancement_attempt(...)` must follow the existing
`with_for_update()` + `version += 1` + `updated_at = utc_now()` discipline
used by `mark_draft_created(...)` and `_try_append_turn(...)`. M9.4D must
specify whether this is a separate write (extra round trip) or folded into
the existing `mark_draft_created(...)` / turn-append transaction for the
outcomes where those already run.

### 4.5 `StorageInterface` boundary

`record_advancement_attempt(...)` is added to `ConversationStateStore`
(outside `StorageInterface`), exactly like `mark_draft_created(...)` in
M9.2B. No `StorageInterface` signature changes.

### 4.6 Explicitly excluded from M9.4D

* No parser prompt or `PROMPT_VERSION` change.
* No UI.
* No idle-boundary policy or session expiry.
* No draft amendment.
* No change to the five `ConversationAdvancementOutcome` values themselves.

## 5. Idle-boundary note

`opened_at` and `last_message_at` already exist on `ConversationSession` and
are sufficient to compute "idle for N hours" at read time (section 3.4,
`is_idle`). **No new persisted field is required for idle-boundary
visibility.**

Idle-boundary *behavior/policy* - whether an idle `open` session should allow
a new conversation to start, how `get_or_create_open_session(...)` and
`get_latest_session_for_customer(...)` routing should change, and what
`expired` means operationally - remains fully deferred to its own future M9.4
slice. M9.4B does not design or implement expiry behavior.

## 6. Architecture constraints (apply to M9.4C and M9.4D)

* Storage remains pure persistence; no business policy in storage rows beyond
  the fields listed above.
* Any new domain-visible shapes are Pydantic/dataclass models, consistent
  with existing `ConversationSession` / `ConversationTurn` /
  `ConversationAdvancementResult` dataclasses.
* `StorageInterface` remains the migration boundary for product/customer/
  order/stock data; conversation observability stays outside it, as M9.2A
  established for `ConversationStateStore` and `ConversationOrderLookup`.
* No parser prompt or `PROMPT_VERSION` change.
* No outbound replies.
* No UI build (no new `pages/*.py`).
* No session expiry implementation.
* No draft amendment after `draft_created`.
* No cleanup of `web/inbound.py` / `create_draft_from_inbound_message(...)`
  (still deferred from M9.3A).
* `live_sheets` is not run for any of M9.4B/C/D.

## 7. Implementation split

### M9.4B - Conversation observability/read-model design

Scope:

* create this design document;
* update `ROADMAP.md`, `CHANGELOG.md`, and
  `docs/M9_CONVERSATION_STATE_ARCHITECTURE.md`.

No code, tests, migrations, or UI beyond this design doc and the docs listed
above.

### M9.4C - Read-only conversation observation/read-model (no schema change)

Status: implemented in `bc2de4a feat(m9): add conversation observation read
model`. See section 11 for implementation notes.

Scope (future):

* `ConversationObservationReads` protocol and
  `PostgresConversationObservationReads` implementation per section 3.
* `ConversationObservationItem` / `ConversationObservationDiagnostics` /
  `ConversationObservationSnapshot` DTOs.
* Tests covering tenant scoping, `turn_count`, `latest_message_sid`,
  `latest_body_preview`, `has_draft`/`linked_order_id`, `is_idle`, and
  `needs_operator_attention`.

Explicitly excluded: schema changes, migrations, UI, changes to
`ConversationAdvancementService` or `web/app.py`.

### M9.4D - Persisted observability hooks (schema + service wiring)

Status: closed, implemented in `1b33d8a feat(m9): add conversation
advancement observability storage` and `eb4c235 feat(m9): record
conversation advancement observability`. See section 12 for implementation
notes.

Scope completed:

* migration `11605e30520d` adds the `latest_advancement_outcome` and
  `latest_parse_error_category` columns chosen per section 4.1;
  `latest_parse_status` was not added (open question resolved);
* `ConversationStateStore.record_advancement_attempt(...)` /
  `PostgresConversationStateStore.record_advancement_attempt(...)`;
* wiring inside `ConversationAdvancementService.advance(...)` via
  `_record_outcome(...)`;
* the `DUPLICATE_MESSAGE` open question (section 4.3) resolved: not
  recorded, no session mutation;
* updated `ALEMBIC_HEAD_REVISION = "11605e30520d"` in
  `tests/test_smoke_preflight.py`.

Explicitly excluded: UI, idle-boundary policy, draft amendment, parser prompt
or `PROMPT_VERSION` changes.

### Idle-boundary behavior/design - separate, later slice

Not part of M9.4B, M9.4C, or M9.4D. Tracked as the second remaining M9.4 scope
item ("idle-boundary behavior/design") in `ROADMAP.md`.

## 8. Required tests (future, per slice)

M9.4C tests (illustrative, written in M9.4C):

* snapshot is tenant-scoped;
* `turn_count` matches `len(list_turns(...))`;
* `latest_message_sid` matches the highest-`sequence_number` turn;
* `has_draft`/`linked_order_id` reflect `resulting_order_id`;
* `is_idle` reflects `now - last_message_at` vs `idle_threshold`;
* `needs_operator_attention` reflects the documented heuristic;
* `PostgresConversationObservationReads` is not added to either AST guard
  set (or, if it is, that the guard still passes with no broad-read
  violations).

M9.4D tests (illustrative, written in M9.4D):

* `record_advancement_attempt(...)` sets `latest_advancement_outcome` and
  `version += 1` under `with_for_update()`;
* `latest_parse_error_category` is set only from the approved safe category
  enum, never raw exception text;
* duplicate `MessageSid` behavior (`DUPLICATE_MESSAGE`) per the resolution of
  the open question in section 4.3;
* `ConversationAdvancementService.advance(...)` remains in
  `ENFORCED_RUNTIME_READ_MODULES` with no new broad-read violations;
* Alembic head expectation in `tests/test_smoke_preflight.py` updated.

## 9. Verification commands

### M9.4B (docs-only)

```powershell
git diff --check
git status --short
```

### M9.4C (future)

```powershell
pytest tests/test_conversation_state_store.py tests/test_conversation_advancement.py -q
pytest tests/test_architecture_boundaries.py -q
pytest -q
ruff check src tests pages
python -m compileall src tests pages
git diff --check
git status --short
```

### M9.4D (future)

```powershell
alembic heads
alembic upgrade head
pytest tests/test_conversation_state_store.py tests/test_conversation_advancement.py tests/test_postgres_models.py -q
pytest tests/test_web_twilio_webhook.py -q
pytest tests/test_architecture_boundaries.py tests/test_smoke_preflight.py -q
pytest -q
ruff check src tests pages
python -m compileall src tests pages
git diff --check
git status --short
```

## 10. Non-goals

* No code, tests, migrations, or UI in M9.4B.
* No idle-boundary/expiry design or implementation in M9.4B.
* No draft amendment.
* No outbound conversational replies.
* No parser prompt or `PROMPT_VERSION` change.
* No `web/inbound.py` / `create_draft_from_inbound_message(...)` cleanup.
* No `StorageInterface` change.
* `live_sheets` not run.

## 11. M9.4C implementation note

M9.4C shipped in `bc2de4a feat(m9): add conversation observation read model`,
matching this design with one field-naming refinement:

* The `ConversationObservationItem` field sketched as `resulting_order_id` in
  section 3.3 was implemented as `linked_order_id`, populated from
  `ConversationSessionRow.resulting_order_id`. `has_draft` is derived as
  `linked_order_id is not None`, as designed.
* The tunable constants from section 3.4 were fixed at
  `ATTENTION_TURN_THRESHOLD = 3`, `LATEST_BODY_PREVIEW_LENGTH = 160`, and
  `DEFAULT_IDLE_THRESHOLD = timedelta(hours=4)`.
* `latest_body_preview` distinguishes "no turns" (`None`) from "latest turn
  has an empty body" (`""`).
* Confirms the section 10-style non-goals for this slice: M9.4C is
  read-only and required no schema change; it does not implement an
  operator page/UI; it does not persist `latest_advancement_outcome` or
  `latest_parse_error_category` (still M9.4D); and it does not implement
  idle/session-expiry behavior - `is_idle` remains a pure read-time
  comparison against `idle_threshold`, not a session-boundary policy.
* Added `tests/test_conversation_observation.py` (17 local SQLite-backed
  tests; no `live_postgres`).

## 12. M9.4D implementation note

M9.4D shipped in `1b33d8a feat(m9): add conversation advancement
observability storage` and `eb4c235 feat(m9): record conversation
advancement observability`, matching this design with the open questions
resolved as follows:

* Section 4.1: only `latest_advancement_outcome` and
  `latest_parse_error_category` were added; `latest_parse_status` was not
  added - `latest_advancement_outcome` (`PARSE_INCOMPLETE` /
  `TURN_APPENDED_INCOMPLETE`) plus `latest_parse_error_category` were
  judged sufficient.
* Section 4.2: `latest_parse_error_category` is restricted to
  `PARSE_ERROR_CATEGORY_VALUES = frozenset({"PARSER_ERROR"})`, defined
  module-level in `conversation_state.py` alongside
  `ADVANCEMENT_OUTCOME_VALUES` (independent of
  `ConversationAdvancementOutcome`, no service-layer import into storage).
  Raw parser/LLM exception text is never persisted.
* Section 4.3: `record_advancement_attempt(...)` is skipped for
  `DUPLICATE_MESSAGE` - the open question is resolved as "recording would
  violate the no-session-mutation guarantee" in
  `docs/M9_2A_CONVERSATION_ADVANCEMENT_SERVICE_DESIGN.md` section 6.
  `ALREADY_HAS_DRAFT` IS recorded for orphan-draft recovery,
  post-`DRAFT_CREATED` follow-up turns, and create-draft-conflict recovery.
* Section 4.4: `record_advancement_attempt(...)` is a separate write - its
  own tenant-scoped `SELECT ... FOR UPDATE`, re-select under lock,
  `version += 1`, `updated_at = utc_now()`, `session.flush()` - not folded
  into `mark_draft_created(...)` or the turn-append transaction.
* Section 4.5: `record_advancement_attempt(...)` was added to
  `ConversationStateStore` / `PostgresConversationStateStore`, outside
  `StorageInterface`, exactly as designed.
* `ConversationAdvancementService.advance(...)` was restructured to a
  single return boundary; `_record_outcome(...)` wraps the
  `record_advancement_attempt(...)` call in try/except, logs
  `logger.warning(..., exc_info=True)` on any failure, and always returns
  the original `ConversationAdvancementResult` unchanged. Observability is
  best-effort and never alters the caller-visible outcome.
* Outcome -> category mapping: `TURN_APPENDED_INCOMPLETE ->
  "PARSER_ERROR"`; `PARSE_INCOMPLETE`, `DRAFT_CREATED`, `ALREADY_HAS_DRAFT`
  -> `None` (clearing any previously recorded category).
* Tests: 7 new tests in `tests/test_conversation_state_store.py`
  (`record_advancement_attempt` validation, locking, and
  version/`updated_at` bumps);
  `test_snapshot_exposes_latest_advancement_outcome_and_parse_error_category`
  added to `tests/test_conversation_observation.py`;
  `test_conversation_sessions_table_is_postgres_only` updated in
  `tests/test_postgres_models.py`; `_SpyConversationStateStore` plus 9 new
  tests added to `tests/test_conversation_advancement.py` covering the
  outcome -> category mapping, the `DUPLICATE_MESSAGE` skip, and
  best-effort failure handling; `ALEMBIC_HEAD_REVISION = "11605e30520d"`
  updated in `tests/test_smoke_preflight.py`.
* Confirms the section 10-style non-goals for this slice: M9.4D persisted
  advancement observability only. It does not implement UI, outbound
  replies, idle/session-expiry behavior, draft amendment, `web/inbound.py`
  cleanup, parser prompt or `PROMPT_VERSION` changes, or `StorageInterface`
  changes; `live_sheets` not run.
