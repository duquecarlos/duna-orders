# M9.4E Idle-Boundary Design and Deferral

Status: design/deferral closed. Runtime idle-boundary expiry is implemented
in M9.6E (committed `93c78e6`, 2026-06-13). See section 5 for updated
runtime behavior.

Baseline: `e84a844 docs(m9): close advancement observability`

M9.4E addresses the second remaining M9.4 scope item identified in
`docs/M9_4B_CONVERSATION_OBSERVABILITY_READ_MODEL_DESIGN.md` section 5 and
`docs/M9_CONVERSATION_STATE_ARCHITECTURE.md` section 2: idle-boundary
behavior/policy for `(tenant_id, customer_phone)` conversation sessions.

An uncommitted runtime implementation attempt was made and then reverted.
This document records the intended policy, the invariant any future
implementation must preserve, why the attempt was deferred, the prerequisite
a future milestone must deliver first, and the current (unchanged) runtime
behavior.

```text
draft -> approved -> confirmed -> atomic inventory commit -> outbound acknowledgement
```

The downstream lifecycle above is unaffected by anything in this document.

## 1. Intended idle policy

The idle boundary is defined as:

```text
received_at - open_session.last_message_at > DEFAULT_IDLE_THRESHOLD
```

* The intended default threshold is `DEFAULT_IDLE_THRESHOLD = timedelta(hours=4)`,
  matching the value already documented in
  `docs/M9_CONVERSATION_STATE_ARCHITECTURE.md` section 2 and already used by
  `ConversationObservationReads` for read-time `is_idle` visibility.
* The idle boundary applies only to sessions with `status="open"`.
* A session with `status="draft_created"` must never auto-expire. Once a
  conversation has produced a draft, it remains the routing target for
  follow-up messages regardless of how much time has passed.
* `status="expired"` and `status="failed"` are terminal and non-routable:
  `_route_session(...)` must not return them, and no store method should
  resume turn-appending or draft creation against them.
* When the idle boundary is crossed, the next inbound message for that
  `(tenant_id, customer_phone)` should start a brand-new conversation:
  a new `conversation_id`, `sequence_number` restarting at `1`, and no
  inherited transcript context from the expired session. The new
  conversation is independent state, not a continuation.
* No new `ConversationAdvancementOutcome` value is intended for idle
  expiry. Idle expiry is a routing-time concern inside `_route_session(...)`,
  not a new outcome reported to the caller.
* No migration is intended. `status="expired"` already exists in
  `ConversationSessionStatus` and `conversation_sessions.status` is already
  `String(40)`, so writing `"expired"` requires no schema change.

## 2. Required invariant

For any `(tenant_id, customer_phone)`, a correct implementation must
preserve:

* At most one *routable* session exists at a time. "Routable" means a
  session that `_route_session(...)` can return and that future inbound
  messages can attach to (`status` in `REACHABLE_SESSION_STATUSES =
  ("open", "draft_created")`).
* A successful `create_draft(...)` for a session must drive that *same*
  session to `status="draft_created"` with `resulting_order_id=<order_id>`.
  A draft must never be created without its producing session reflecting
  that draft.
* Idle expiry must not create a new `open` session for a customer while a
  draft completion for that customer's prior session can still land. In
  other words, opening a new session for a customer must not race ahead of
  that customer's in-flight `create_draft` / `mark_draft_created` for the
  session being replaced.
* `mark_draft_created(...)` must never silently no-op into an
  expired/unlinked row. If the session it is asked to mark is no longer the
  customer's routable session (for example because idle expiry already
  opened a new session), the implementation must not simply skip the write
  and let the caller believe a draft was linked when the linking session is
  now unreachable.
* If a `draft_created` session exists for a customer, the next inbound
  message must route to it and return `ALREADY_HAS_DRAFT`, not start fresh
  parsing against a different (newer) session.
* `get_latest_session_for_customer(...)` is the routing authority used by
  `_route_session(...)`. A correct implementation must therefore ensure
  either:
  * a `draft_created` session wins "latest" over a later `open` session for
    the same customer, so routing still finds it; or
  * the competing `open` session is never created in the first place while
    a `draft_created` session for that customer exists.

## 3. Why runtime M9.4E was deferred

A runtime implementation was attempted using
`pg_advisory_xact_lock`-based customer locking inside
`PostgresConversationStateStore` methods (`mark_draft_created(...)` and a
new `get_or_create_open_session_after_idle_boundary(...)` /
`_try_idle_boundary_transition(...)` pair), wired into
`ConversationAdvancementService._route_session(...)`.

A deterministic test
(`test_draft_created_session_remains_latest_over_later_open_session_for_customer`,
now kept as an xfail acceptance test - see section "Acceptance test" below)
proved that this design still allowed the invalid terminal state from
section 2: an `old` session ends as `draft_created` while a `new` session
for the same `(tenant_id, customer_phone)` exists with `status="open"` and is
`get_latest_session_for_customer(...)`'s answer - so the next inbound message
would route to `new` and start fresh parsing instead of returning
`ALREADY_HAS_DRAFT` against `old`.

The root cause is structural, not a tuning bug:

* `PostgresConversationStateStore` methods are each self-contained: every
  method opens its own `session_scope(self._session_factory)`, which creates
  a new `Session`/connection, commits or rolls back, and closes - all within
  that one method.
* `pg_advisory_xact_lock` is transaction-scoped. A lock taken inside one
  store method's transaction is released when that method's `session_scope`
  exits, before the next method runs.
* The dangerous lifecycle spans *multiple* store/service calls:
  `_route_session(...)` -> `append_turn_if_new(...)` -> parser/LLM `parse(...)`
  -> `create_draft(...)` -> `mark_draft_created(...)` ->
  `record_advancement_attempt(...)`. A transaction-scoped advisory lock
  acquired inside any single one of these cannot protect the others.
* A naive service-level fix - acquiring a session-scoped advisory lock
  (`pg_advisory_lock`/`pg_advisory_unlock`) for the whole `advance(...)` call
  - can self-deadlock: the inner store methods open their *own* connections
  via `session_factory()`, so if those connections try to acquire the same
  advisory key, they block behind the outer lock held on a different
  connection from the same logical caller.

Correct runtime idle-boundary behavior therefore requires an explicit
per-customer serialization/unit-of-work boundary that spans the full
advancement lifecycle - not a lock added inside one store method. Designing
and implementing that boundary is a larger architectural change and is out
of scope for M9.4E.

## 4. Future prerequisite: lifecycle-spanning unit of work for conversation advancement

Runtime idle-boundary expiry should not be attempted again until a future
milestone delivers a **lifecycle-spanning unit of work for conversation
advancement**: a way to safely serialize one customer's advancement
lifecycle - `_route_session(...)`, `append_turn_if_new(...)`, the
parse/completeness decision, `create_draft(...)`,
`mark_draft_created(...)`, and `record_advancement_attempt(...)` - across
the independent transactions those steps currently use.

The central open design issue is that this lifecycle includes parser/LLM
calls, which involve real network latency. Naively holding one database
transaction (and an advisory lock tied to that transaction's connection)
open across the full LLM call would pin a database connection and serialize
a customer behind an external API call for the duration of that call - a
significant latency and connection-pool cost for every conversational turn.

A future design must resolve this by doing one of:

* separating customer-level serialization from database-transaction
  duration, so the serialization mechanism does not require holding a
  database connection/transaction open for the LLM call; or
* re-validating session state under a customer lock *after* parsing
  completes and *before* `create_draft(...)`/`mark_draft_created(...)` run,
  so the lock is held only around the short, database-only commit window,
  with a defined recovery path if state changed while parsing was in
  flight; or
* introducing a durable customer lock/queue mechanism (for example, a
  per-customer claim row with its own lifecycle) that survives across the
  multiple `session_scope(...)` calls without depending on
  connection-scoped Postgres advisory locks.

Idle-boundary expiry (section 1) and the invariant in section 2 should be
implemented as part of, or immediately after, that unit-of-work milestone,
not before it.

## 5. Current behavior after M9.6E (committed `93c78e6`)

M9.6E implemented the runtime idle-boundary expiry described in section 1.
The following replaces the post-M9.4E behavior snapshot:

* `_route_session(...)` in `ConversationAdvancementService` now lazily
  expires idle `open` sessions: if `received_at - last_message_at >
  DEFAULT_IDLE_THRESHOLD` and `status == "open"`, `expire_session(...)` is
  called and the advance is routed to a new `open` session. Sessions with
  `status="draft_created"` are never idle-expired (positive `== "open"`
  guard).
* `expire_session(*, tenant_id, conversation_id)` is now live on
  `ConversationStateStore` Protocol and `PostgresConversationStateStore`.
  It acquires `WITH FOR UPDATE`, sets `status="expired"`, increments
  `version`, and updates `updated_at`. It is idempotent: no-ops if already
  `expired`. It does not mutate `last_message_at`.
* `get_latest_session_for_customer(...)` now filters to
  `status.in_(("open", "draft_created"))` and orders with a
  `case((status == "draft_created", 0), else_=1)` prefix so a
  `draft_created` session wins over a later `open` session.
* `mark_draft_created(...)` is unchanged: marks `status="draft_created"` and
  `resulting_order_id=order_id`, idempotent for the same order, raises
  `ValueError` for a different order.
* `DEFAULT_IDLE_THRESHOLD = timedelta(hours=4)` is imported from
  `conversation_observation.py`. Relocation to lifecycle/tenant config is
  deferred; see `DECISIONS.md` "M9.6E - Idle threshold source is deferred
  from lifecycle config".
* No Alembic migration. Alembic head stays `d60b084798e0`. No
  `StorageInterface` change.

## Acceptance test

`tests/test_conversation_state_store.py::test_draft_created_session_remains_latest_over_later_open_session_for_customer`
was kept as a `strict=True` xfail acceptance test; it reproduced the invalid
terminal state from section 2 using only public `ConversationStateStore` APIs:

1. `old = get_or_create_open_session(...)` then `mark_draft_created(...)` —
   `old` is `draft_created`.
2. `new = get_or_create_open_session(...)` for the same
   `(tenant_id, customer_phone)` five hours later — `new` is `open` and a
   different session.
3. `get_latest_session_for_customer(...)` returned `new` instead of `old`
   because ordering had no status preference.

M9.6E D2 (`case((status == "draft_created", 0), else_=1)` ORDER BY prefix)
makes this test pass. The `@pytest.mark.xfail(strict=True)` decorator was
removed in commit `93c78e6`. The test now passes unconditionally.
