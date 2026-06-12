# M9.4E Idle-Boundary Design and Deferral

Status: design/deferral only. Runtime idle-boundary expiry is NOT
implemented.

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

## 5. Current behavior after M9.4E

* Runtime behavior is unchanged from `e84a844`:
  `src/duna_orders/storage/conversation_state.py`,
  `src/duna_orders/storage/conversation_observation.py`, and
  `src/duna_orders/services/conversation_advancement.py` are byte-identical
  to that baseline.
* `mark_draft_created(...)` retains its exact pre-M9.4E behavior: it marks
  `status="draft_created"` and `resulting_order_id=order_id` regardless of
  the row's current status, is idempotent when called again with the same
  `order_id`, and raises `ValueError` if the session is already linked to a
  different order.
* `ConversationObservationReads.get_conversation_observation_snapshot(...)`
  still computes read-time `is_idle` exactly as it did before M9.4E
  (`now - last_message_at > DEFAULT_IDLE_THRESHOLD`, defined locally in
  `conversation_observation.py`). This is unchanged read-time visibility, not
  a session-boundary policy.
* No runtime code path writes `status="expired"`. `"expired"` remains a
  defined value of `ConversationSessionStatus` and a valid
  `conversation_sessions.status` value, but nothing sets it.
* M9.4E is closed as design/deferral only: no runtime idle-boundary expiry,
  no migration, no `StorageInterface` change, and no UI.

## Acceptance test

`tests/test_conversation_state_store.py::test_draft_created_session_remains_latest_over_later_open_session_for_customer`
is kept as a `strict=True` xfail acceptance test for a future
implementation. It uses only baseline (pre-M9.4E) public
`ConversationStateStore` APIs to reproduce the invalid terminal state from
section 2:

1. `old = get_or_create_open_session(...)` then
   `mark_draft_created(tenant_id=..., conversation_id=old.conversation_id,
   order_id=...)` - `old` is now `draft_created`.
2. `new = get_or_create_open_session(...)` for the same
   `(tenant_id, customer_phone)` five hours later - `new` is `open` and is a
   *different* session, per existing baseline behavior
   (`test_get_or_create_open_session_opens_new_session_after_draft_created`).
3. `get_latest_session_for_customer(...)` currently returns `new` (`open`),
   not `old` (`draft_created`), because ordering is purely
   `last_message_at DESC, updated_at DESC, opened_at DESC,
   conversation_id DESC` with no status preference.

A future implementation must make this test pass (by satisfying section 2's
invariant) without weakening it; the test is `strict=True` so an unexpected
pass (XPASS) fails the suite until the `xfail` marker is removed.
