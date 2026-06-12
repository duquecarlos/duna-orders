# M9.6A Conversation Advancement Unit-of-Work Design

Status: design only. No runtime behavior, migration, or `StorageInterface`
change is implemented in this slice.

Baseline: `d27caa2 feat(ui): add conversation detail view`

M9.6A addresses the future prerequisite identified in
`docs/M9_4E_IDLE_BOUNDARY_DESIGN.md` section 4 and
`docs/M9_CONVERSATION_STATE_ARCHITECTURE.md` section "future prerequisite":
a **lifecycle-spanning unit of work for conversation advancement**, scoped
per `(tenant_id, customer_phone)`. This document records the design for that
unit of work: the contested resource and serialization key, the invariants
a future implementation must guarantee, the strategies considered, the
recommended strategy, a future schema concept, the future `advance()`
integration shape, idle expiry as the first consumer, retry/error semantics,
a conformance checklist, and future acceptance tests.

No runtime code, migration, or test changes are made in M9.6A. This document
is the artifact.

## 1. Problem statement

M9.5 closed operator conversation visibility: a read-only Streamlit page
backed by `PostgresConversationObservationReads.get_conversation_observation_snapshot(...)`
and `get_conversation_observation_detail(...)`. That milestone was safe to
ship without any serialization design because it adds **no new writers**: it
only reads existing `conversation_sessions` / `conversation_turns` rows
through tenant-scoped, read-only queries.

The next feature category is different in kind. Draft amendment, outbound
WhatsApp replies, runtime idle expiry, and the payment/confirmation flow are
all **mutating** conversation behaviors that read and write
`conversation_sessions` / `conversation_turns` (and downstream `orders`)
state as part of the same logical "a customer sent a message, decide what
happens next" operation that `ConversationAdvancementService.advance(...)`
already performs. Before any of those features can be designed safely, the
concurrency model for `advance(...)` itself needs to be settled - otherwise
each new mutating feature would have to independently reinvent (or, worse,
ignore) per-customer serialization.

### M9.4E idle-boundary deferral

`docs/M9_4E_IDLE_BOUNDARY_DESIGN.md` attempted exactly this for one specific
mutating feature - runtime idle-boundary expiry - and was deferred. The
attempted implementation used `pg_advisory_xact_lock`-based customer locking
inside individual `PostgresConversationStateStore` methods
(`mark_draft_created(...)` and a new
`get_or_create_open_session_after_idle_boundary(...)` /
`_try_idle_boundary_transition(...)` pair). A deterministic test
(`test_draft_created_session_remains_latest_over_later_open_session_for_customer`,
kept as a `strict=True` xfail acceptance test - see section 2) proved this
still allowed an invalid terminal state: a `draft_created` session ("old")
loses "latest" status to a later `open` session ("new") for the same
`(tenant_id, customer_phone)`, so the next inbound message would route to
`new` and start fresh parsing instead of returning `ALREADY_HAS_DRAFT`
against `old`.

### Why store-method-level locks and `pg_advisory_xact_lock` are insufficient

`pg_advisory_xact_lock` releases automatically when the transaction that
acquired it commits or rolls back. Every
`PostgresConversationStateStore` / `PostgresConversationOrderLookup` /
`OrderService`-internal store method opens its own
`session_scope(self._session_factory)` - its own `Session`, its own
transaction, its own connection - and that transaction commits before the
method returns. A `pg_advisory_xact_lock` taken inside any one of these
methods is therefore released **before the next method in the lifecycle even
begins**. Since the dangerous lifecycle spans *multiple* such methods
(`get_latest_session_for_customer` -> `get_or_create_open_session` ->
`append_turn_if_new` -> parser/LLM `parse(...)` -> `create_draft(...)`
internals -> `mark_draft_created(...)` -> `record_advancement_attempt(...)`),
a lock confined to any single step provides zero mutual exclusion against
any of the others. This is a structural property of the current plumbing,
not a tuning bug - see section 2 and the M9.6 pre-flight.

A naive alternative - acquiring a **session-scoped** advisory lock
(`pg_advisory_lock`/`pg_advisory_unlock`, tied to one held connection) for
the whole `advance(...)` call - is also rejected; see Strategy A in
section 5.

### Why M9.5 visibility was safe but the next features need serialization first

M9.5's read-only queries are safe under arbitrary concurrent `advance(...)`
calls because SQL reads against committed rows are always consistent at the
statement level, and the observation reads never write. The next features
all need to **make and commit a decision** (create a draft, send an outbound
reply, expire a session, record a payment) based on the *current* state of a
customer's conversation, and that decision must not be invalidated by another
in-flight decision for the same customer landing concurrently. M9.6 exists to
define, once, how that "decide and commit" exclusivity works for one customer
at a time - so that M9.7+ (idle expiry), draft amendment, outbound replies,
and payment/confirmation can each be built on top of it instead of each
re-deriving (or skipping) the same analysis.

## 2. Current runtime facts

These facts were confirmed by direct inspection of
`src/duna_orders/storage/postgres_session.py`,
`src/duna_orders/storage/conversation_state.py`,
`src/duna_orders/storage/conversation_orders.py`,
`src/duna_orders/services/conversation_advancement.py`, and
`src/duna_orders/services/orders.py` as part of the M9.6 pre-flight, and are
unchanged as of baseline `d27caa2`.

* **`session_scope(session_factory)`**
  (`src/duna_orders/storage/postgres_session.py:86-97`) opens a brand-new
  `Session` from `session_factory()`, yields it, commits on normal exit or
  rolls back on exception, and **always closes the session** before
  returning. Every store method that uses it - roughly 50 call sites across
  `postgres.py`, `conversation_state.py`, `conversation_orders.py`,
  `order_lifecycle.py`, `order_confirmation.py`, `outbound_messages.py`,
  `processed_messages.py`, and `conversation_observation.py` - gets its own
  connection and its own transaction, scoped to that one method call.
* **No shared connection/session seam exists.** There is no parameter,
  thread-local, contextvar, or other mechanism by which one store method can
  hand its `Session`/connection to another store method, or by which a
  service can request "give me a session and let me call multiple store
  methods against it."
* **No unit-of-work abstraction exists.** A repository-wide search for
  `unit.of.work` / `UnitOfWork` / `begin_nested` finds nothing. The only
  cross-call coordination primitive present today is row-level
  `SELECT ... FOR UPDATE` (`conversation_state.py:286,333,412`, inside
  `mark_draft_created`, `record_advancement_attempt`, and
  `_try_append_turn`), and each of those locks is acquired and released
  within that one method's own transaction.
* **`advance(...)` spans many independent transactions.** As defined in
  `src/duna_orders/services/conversation_advancement.py:62`, a single call to
  `advance(...)` can touch, in order: `get_latest_session_for_customer`,
  `get_or_create_open_session`, `append_turn_if_new`,
  `get_order_by_conversation_id` (orphan-draft recovery), `list_turns`,
  `list_products` (via `TenantScopedReadService`), the parser/LLM `parse(...)`
  call, `create_draft(...)` internals (`get_product`,
  `get_customer_by_phone`, optional `create_customer`, `create_order` /
  `create_order_with_transition` - each its own transaction),
  `mark_draft_created`, and finally `record_advancement_attempt`. Each of
  these is its own `session_scope` transaction; none share a connection.
* **The parser/LLM call is outside any DB transaction.** In
  `_advance_open_session(...)`, `self._parsing_service.parse(...)` runs after
  `list_turns`/`list_products` (each already committed and closed) and before
  `create_draft(...)` (which opens its own new transactions). No database
  transaction or connection is held open across the parser call today.
* **M9.4E xfail location and invariant**:
  `tests/test_conversation_state_store.py::test_draft_created_session_remains_latest_over_later_open_session_for_customer`
  (line ~605), marked
  `@pytest.mark.xfail(reason="M9.4E idle expiry deferred: needs lifecycle-spanning per-customer serialization; see docs/M9_4E_IDLE_BOUNDARY_DESIGN.md", strict=True)`.
  It documents: once a session for `(tenant_id, customer_phone)` reaches
  `draft_created`, it must remain `get_latest_session_for_customer(...)`'s
  answer even if a later `open` session is created for the same customer
  (e.g., by a future idle-boundary transition racing with an in-flight
  `advance(...)` that is about to complete that customer's draft). Today
  `get_latest_session_for_customer` orders purely by
  `last_message_at DESC, updated_at DESC, opened_at DESC,
  conversation_id DESC`, with no status preference, so a later `open` session
  always outranks an earlier `draft_created` one - the test is `strict=True`
  xfail until a future implementation fixes this.

## 3. Contested resource and serialization key

**Primary serialization key for now: `tenant_id + customer_phone`.**

This pairing is exactly the key already used by
`get_latest_session_for_customer(tenant_id=..., customer_phone=...)` and
`get_or_create_open_session(tenant_id=..., customer_phone=...)` - the two
calls that decide *which conversation* an inbound message belongs to. The
contested resource is "the set of conversation sessions for this customer
and which one is routable/latest," not any single session row.

A future implementation should encapsulate this key behind a single helper,
e.g.:

```python
def conversation_customer_key(tenant_id: str, customer_phone: str) -> str:
    """Stable serialization key for one customer's conversation lifecycle."""
```

so that every call site (claim acquisition, lease lookups, observability)
derives the key the same way, and the key's definition can evolve (see
"future cleaner key" below) without touching call sites.

### Why `conversation_id` is the wrong key

`conversation_id` identifies one *session* - the contested resource itself,
not the lock around it. The M9.4E xfail scenario is precisely a case where
two different `conversation_id`s (`old`, `draft_created`, and `new`, `open`)
both belong to the same customer and **both** must be considered by the
serialization mechanism at the same time: the decision "should `new` exist as
the customer's routable session, given that `old` may still be completing a
draft?" cannot be made by locking either `conversation_id` alone, because at
the moment that decision must be made, `new` may not exist yet (it is the
thing being created) and `old` is the thing whose in-flight completion must
not be raced. Locking on `conversation_id` would let a lock-acquisition for
`new` proceed entirely independently of a lock held on `old`, because they
are different keys - exactly the failure mode M9.4E hit. The lock must be
keyed on the customer, *above* the session level, so that "create a new
session for this customer" and "finish this customer's existing session's
draft" contend for the same key.

### Why `tenant_id + customer_id` is the future cleaner key

`customer_phone` is a string carried through from the inbound WhatsApp
`From` field (`from_number` in `advance(...)`) and stored verbatim on
`conversation_sessions.customer_phone`. There is no first-class "customer is
the owner of this conversation lifecycle" entity wired into conversation
routing today - `Customer` rows exist (created by `OrderService.create_draft`
via `get_customer_by_phone` / `create_customer`) but conversation routing
does not look them up or key off `customer_id`. Once such a model exists
(i.e., conversation sessions are linked to a `customer_id` the same way
orders are), `tenant_id + customer_id` becomes the cleaner key: it is a
stable surrogate key rather than a free-form string, immune to phone-format
drift (see below), and aligns the conversation-lifecycle lock with the same
identity used for order/customer records. `conversation_customer_key(...)`
is the seam where this future swap would happen without touching call sites.

### Phone normalization assumptions and risks

`conversation_sessions.customer_phone` is populated directly from
`advance(...)`'s `from_number` parameter (e.g.
`"whatsapp:+573001112222"`), **unnormalized**. Separately,
`OrderService.create_draft(...)` calls
`normalize_customer_phone(...)` (`src/duna_orders/domain/phone.py:4-10`,
which strips spaces and hyphens) before looking up/creating the `Customer`
row for `request.customer_phone`. These are two different strings derived
from the same logical phone number, used in two different places.

For `conversation_customer_key(tenant_id, customer_phone)` to correctly
serialize *all* of a customer's conversation activity, every caller that
derives the key must use the **same** representation of `customer_phone` as
`conversation_sessions.customer_phone` / `_route_session(...)` already use -
i.e., the raw, unnormalized `from_number` as received from Twilio, not the
`normalize_customer_phone(...)` output. In practice Twilio's `From` field is
already consistently formatted per number, so this is unlikely to cause
collisions in normal operation, but it is a latent risk if:

* a future feature derives the lock key from a *normalized* phone (e.g., to
  match `Customer.customer_phone`) while routing still uses the raw
  `from_number` - the two would compute different keys for the same customer
  and fail to serialize against each other; or
- Twilio ever sends the same logical number in two different raw formats for
  the same customer (e.g., with/without whitespace) - routing already treats
  these as different customers today (`get_or_create_open_session` matches on
  exact `customer_phone` string), so this is a pre-existing routing risk, not
  one introduced by M9.6, but it would also produce two different lock keys.

This document records the risk; resolving it (e.g., normalizing
`customer_phone` consistently at ingestion, or switching the lock key to
`customer_id` once available) is out of scope for M9.6A and is a candidate
follow-up for the `tenant_id + customer_id` key transition above.

## 4. Required invariants

A future implementation of the conversation-advancement unit of work must
guarantee:

* **Duplicate `MessageSid` idempotency remains the first gate.** A retried
  webhook delivery for an already-processed `message_sid` must be detected
  and short-circuited without needing to participate in per-customer
  serialization at all (see sections 8 and 10).
* **Only one conversation advancement lifecycle for a given
  `tenant_id + customer_phone` can commit state-changing decisions at a
  time.** "State-changing decisions" means: opening/choosing a session,
  appending a turn, creating a draft, marking a session `draft_created`,
  expiring a session, and recording the advancement outcome. Reads (e.g.
  M9.5 observation queries) are unaffected and remain lock-free.
* **Different customers must not block each other.** The serialization key
  is per `(tenant_id, customer_phone)`; lifecycles for different customers
  (even within the same tenant) must proceed fully concurrently.
* **The parser/LLM call must not be inside a long-held DB
  transaction.** Whatever holds the per-customer lifecycle "claimed" across
  the parser call must not require an open database transaction or pinned
  connection for the duration of that call.
* **After parse, state must be revalidated before writing draft/session
  outcomes.** Because the parser call happens without an open transaction
  (and, depending on the chosen strategy, without a DB-enforced lock), the
  session state the parser's output was based on may be stale by the time
  parsing completes. The implementation must re-read the relevant session
  state under a short transaction, immediately before
  `create_draft`/`mark_draft_created`, and detect/handle the case where it
  changed.
* **Runtime idle expiry must not race draft creation.** An idle-boundary
  transition that would open a new `open` session for a customer must not
  be able to commit while that customer's prior session is in the middle of
  completing a draft (the M9.4E section-2 invariant).
* **A `draft_created` session must remain the authoritative latest session
  over a later `open` session for the same customer when that later `open`
  session is caused by stale/idle-boundary behavior.** This is the exact
  property the M9.4E xfail test checks; it must hold under the new unit of
  work (whether by making `get_latest_session_for_customer(...)` prefer
  `draft_created`, or by the serialization mechanism preventing the
  competing `open` session from ever being created - see section 9).
* **`latest_advancement_outcome` / `latest_parse_error_category` remain
  observable and are written consistently.** `record_advancement_attempt(...)`
  must continue to run for every lifecycle that reaches a terminal outcome
  (including failure/abort paths), so M9.5's observation reads continue to
  reflect the true latest outcome - no lifecycle should "disappear" from
  observability because it held a claim and then errored.

## 5. Strategy options considered

### A. Shared DB session / session-scoped advisory lock across full `advance()`

* **Pros**: conceptually simplest - one transaction means every read inside
  it sees a consistent snapshot, and a `pg_advisory_lock` held on that one
  connection genuinely spans the whole lifecycle without the
  per-transaction-release problem of `pg_advisory_xact_lock`.
* **Cons**: requires passing one `Session`/connection into every store method
  the lifecycle touches - `get_latest_session_for_customer`,
  `get_or_create_open_session`, `append_turn_if_new`,
  `get_order_by_conversation_id`, `list_turns`, `list_products`,
  `create_draft`'s internals (`get_product`, `get_customer_by_phone`,
  `create_customer`, `create_order`/`create_order_with_transition`),
  `mark_draft_created`, `record_advancement_attempt` - across multiple store
  classes (`PostgresConversationStateStore`, `PostgresConversationOrderLookup`,
  `PostgresStorage`, `OrderLifecycleStore`). That is a broad
  transaction/session-management refactor, not a "small seam."
* **Why current plumbing does not support it**: every one of those methods
  calls `session_scope(self._session_factory)` (or, for
  `_try_create_open_session`, `self._session_factory()` directly) - there is
  no optional "use this session instead" parameter anywhere, and adding one
  to ~10+ methods across 4+ classes is itself a significant change with its
  own test surface.
* **Why holding a DB connection across parser/LLM latency is undesirable**:
  as already documented in `docs/M9_4E_IDLE_BOUNDARY_DESIGN.md` section 4 -
  it pins one connection from the pool, and any row locks taken earlier in
  the transaction (e.g. the `FOR UPDATE` in `_try_append_turn`) remain held,
  for the entire duration of an external network call whose latency is
  outside the application's control. Under load this both starves the
  connection pool and turns parser slowness into database contention.

### B. Transaction-scoped advisory lock per store method

* **Pros**: trivial to add - a single `SELECT pg_advisory_xact_lock(...)`
  inside an existing `session_scope` block, with no signature changes
  anywhere; the lock auto-releases on commit/rollback, so there is no
  explicit unlock or leak-on-crash concern.
* **Cons**: provides protection only for the duration of that one method's
  transaction - i.e., milliseconds - which ends before the next method in the
  lifecycle is even called.
* **Why this failed conceptually in M9.4E**: this is exactly the design that
  was attempted and reverted (`pg_advisory_xact_lock` inside
  `mark_draft_created(...)` and the idle-boundary transition methods). The
  xfail acceptance test demonstrates the gap directly: the lock inside
  `mark_draft_created` for `old` is released by the time
  `get_or_create_open_session` for `new` runs (a separate transaction,
  separate lock acquisition, no contention because the first lock is already
  gone).
* **Why it cannot serialize the full lifecycle**: the lifecycle is a sequence
  of independent transactions (section 2); a lock that lives and dies inside
  one of them cannot create mutual exclusion with any of the others, by
  construction. No amount of adding more per-method locks fixes this -
  the *gaps between* the locked methods are exactly where the race occurs.

### C. Post-parse short critical section with revalidation

* **Pros**: requires no lock or claim to be held across the parser/LLM call
  at all - the "expensive" part of the lifecycle runs with zero locking
  overhead. The locked window (re-read session state, then
  `create_draft`/`mark_draft_created`/`record_advancement_attempt`) is short
  and entirely database-local.
* **Cons**: on its own, provides **no protection for the pre-parse portion**
  of the lifecycle. Two concurrent `advance(...)` calls for the same customer
  (e.g., two inbound messages processed by two webhook workers, or an
  in-flight `advance(...)` racing a future idle-expiry transition) can both
  pass `_route_session`/`append_turn_if_new` and both proceed into parsing
  before either reaches the critical section - duplicating parser work, and,
  more importantly, not preventing the M9.4E race itself: the idle-expiry
  transition that creates `new` is not, by itself, gated by anything in this
  strategy, so it can still commit `new` as `open` and "latest" while `old`'s
  critical section has not yet run.
* **What it solves**: guarantees that the *final* state-changing writes
  (`create_draft`, `mark_draft_created`, `record_advancement_attempt`) are
  made against session state that has just been re-checked, so a lifecycle
  that discovers its session is no longer the one it should be writing to
  (e.g., because something else already completed a draft, or expired the
  session) can abort/recover instead of silently overwriting.
* **What it does not solve by itself**: full mutual exclusion across the
  whole lifecycle, and specifically the M9.4E section-2 invariant (idle
  expiry must not race draft completion) - that requires *something* to also
  serialize the idle-expiry transition itself against an in-flight
  `advance(...)` for the same customer, which is what strategy D provides.
  C is best understood as a **component** of the recommended strategy (the
  "revalidate before committing" step), not a complete strategy on its own.

### D. Durable per-customer claim/lock row with lease semantics

* **Pros**:
  * Does not require connection-scoped advisory locks or a shared
    session/connection across the lifecycle - the "lock" is committed,
    visible row state, checkable from any connection.
  * Fits the existing `session_scope`-per-call pattern: acquiring,
    renewing, and releasing the claim are each just one more short
    `session_scope` call (an upsert/update on a new table), exactly like
    every existing store method. No new connection-sharing seam is needed.
  * Survives process crashes - a held claim with an expired lease can be
    reclaimed by a later attempt, without manual cleanup.
  * Can be "held" across the parser/LLM call purely as committed DB state
    that other lifecycles check before proceeding, with no connection or
    transaction open during the call itself.
  * Serializes the **entire** lifecycle, including the pre-parse routing
    step that strategies B and C (alone) cannot protect - any code path
    that wants to open/choose a session, append a turn, create a draft, mark
    a session `draft_created`, or expire a session for a customer must first
    hold that customer's claim row.
* **Cons**:
  * Requires a new table (future migration - explicitly out of scope for
    M9.6A; see section 7).
  * The "lock" is enforced by convention at the service layer (every mutating
    code path must acquire the claim first), not automatically by the
    database the way a row lock inside a single transaction is - a future
    code path that forgets to acquire the claim silently bypasses
    serialization. This must be covered by a guard/test (see sections 11-12).
  * Lease-based recovery introduces a tunable lease duration and a clock-skew
    consideration: too short risks reclaiming a still-live holder's claim
    (two lifecycles running "simultaneously" after all); too long delays
    recovery after a crash.
  * Abandoned claims (crashed workers) need a defined reclaim path -
    "next requester for that customer, after lease expiry, may take over" -
    which must be specified precisely enough to avoid double-processing (see
    section 10).
* **Required schema (future implementation)**: see section 7.
* **Fit with current short `session_scope` pattern**: acquire = one
  short transaction that inserts-or-updates a row keyed by
  `(tenant_id, conversation_customer_key(tenant_id, customer_phone))` with a
  `lease_expires_at` in the near future, succeeding only if no live
  (non-expired) claim exists for that key; release = one short transaction
  that clears/deletes that row; optional renew = one short transaction that
  extends `lease_expires_at` for a long-running lifecycle (e.g. slow parser
  call). None of these require holding a session open between calls.
* **Serializing without holding a transaction across the LLM call**: once
  the claim row is committed (acquire transaction committed and closed), the
  *fact* that this lifecycle holds the claim is durable and checkable by
  anyone - including the lifecycle itself, when it comes back after the
  parser call, simply by re-reading its own claim row (still valid if not
  expired). No connection needs to remain open in between.
* **Lease timeout / recovery at a design level**: each claim row carries
  `lease_expires_at = acquired_at + LEASE_DURATION` (and `updated_at` on
  renewal). A lifecycle holds its claim as long as `lease_expires_at` is in
  the future. If a lifecycle crashes mid-flight, its claim row remains but
  `lease_expires_at` eventually passes; a later `advance(...)` for the same
  customer that tries to acquire the claim and finds an existing row whose
  lease has expired may treat the prior holder as dead and take over
  (overwriting `holder_id`/`acquired_at`/`lease_expires_at`). Recovery is
  "the next request for that customer gets to proceed," not automatic
  resumption of the abandoned lifecycle's partial work - which is why
  idempotency (duplicate `MessageSid`) and revalidation (strategy C) must
  both be in place: the abandoned lifecycle's partial side effects, if any,
  must be safe to either ignore or supersede.

## 6. Recommended strategy

**Recommended: Durable per-customer claim/lock row (strategy D), with short
DB transactions for each step, and a post-parse short critical section with
revalidation (strategy C) as the final safeguard before committing
draft/session outcomes.**

### Justification

* It is the only option that serializes the **full** lifecycle - including
  the pre-parse routing/append steps where the M9.4E race actually originates
  - without requiring a shared connection/session across that lifecycle
  (rejecting A) and without the transaction-scoped-release problem that sank
  the M9.4E attempt (rejecting B-alone).
* It does not hold a database transaction, connection, or row lock across the
  parser/LLM call - the claim is committed row state, not a connection-bound
  lock - directly resolving the M9.4E section-4 "central open design issue."
* It fits the codebase's existing idiom: every store method is already a
  short, independent `session_scope` call; acquiring/renewing/releasing a
  claim is just more of the same, rather than introducing a new
  connection-passing convention throughout the storage layer.
* Revalidation (C) is retained as defense-in-depth even though D closes the
  main race: it protects against lease-duration edge cases (e.g., a claim
  expiring and being reclaimed while the original holder is still slow but
  about to finish) by making the final commit conditional on the session
  state still being what the lifecycle expects.

### Future implementation shape

1. **Duplicate `MessageSid` gate runs first, outside the customer claim.** A
   fast existence check by `(tenant_id, message_sid)` short-circuits retried
   webhook deliveries before any claim is acquired.
2. **Acquire the per-customer claim** for
   `conversation_customer_key(tenant_id, customer_phone)`. If another
   lifecycle already holds a live (non-expired) claim for this customer, this
   attempt waits/retries or returns a "busy, try again" outcome - see
   section 10.
3. **Route/open/append turn under the serialized lifecycle**: with the claim
   held, run `_route_session(...)` (`get_latest_session_for_customer`,
   `get_or_create_open_session`) and `append_turn_if_new(...)` exactly as
   today, each its own short transaction - but now guaranteed that no other
   lifecycle for this customer is concurrently doing the same.
4. **Run the parser while holding the logical claim, but not holding an open
   DB transaction.** `list_turns`/`list_products` (short transactions) and
   then `parse(...)` (no transaction) run as today; the claim row remains
   valid (lease not yet expired) throughout.
5. **After the parser returns, re-read and revalidate session state** under a
   short transaction, before `create_draft`/`mark_draft_created`: confirm the
   session this lifecycle is about to write to is still the customer's
   routable session and still in the state this lifecycle expects (e.g.,
   still `open`, not concurrently moved to `draft_created` or expired by
   another process - which should not be possible while this lifecycle holds
   the claim, but is checked anyway per strategy C).
6. **Commit the draft/session outcome**: `create_draft(...)` internals and
   `mark_draft_created(...)`, each its own short transaction, as today.
7. **Record the advancement outcome**: `record_advancement_attempt(...)`,
   within or immediately after the lifecycle (see section 8 for failure
   semantics).
8. **Release the claim.** If the process dies before releasing, the lease
   expires and a future attempt for this customer can recover (section 10).

## 7. Future schema concept

Design only - **no migration is added in M9.6A**. A future implementation
might introduce a table such as `conversation_customer_claims` (or
`conversation_customer_locks`) with columns along these lines:

* `tenant_id` - tenant scoping, matching every other conversation table.
* `customer_key` - the value of
  `conversation_customer_key(tenant_id, customer_phone)`; for the initial
  implementation this is expected to be `customer_phone` itself (see
  section 3), but stored under a neutral name so the key can evolve to
  `customer_id` later without a column rename.
* `holder_id` - an opaque identifier for the lifecycle/process/request
  currently holding the claim (e.g., a UUID generated per `advance(...)`
  call), used to detect "is this still my claim" on renew/release.
* `acquired_at` - when the current holder acquired the claim.
* `lease_expires_at` - when the current holder's claim becomes reclaimable by
  another lifecycle.
* `updated_at` - bumped on acquire/renew/release, for observability and
  debugging.
* optionally `last_error` / `metadata` (e.g., a JSON column) - for recording
  why a lifecycle released early (parser error, revalidation failure, etc.),
  useful for the observability outcomes described in section 10.

**Uniqueness**: `UNIQUE (tenant_id, customer_key)` - at most one claim row
per customer per tenant, which is both the uniqueness constraint and the
mechanism by which "acquire" either inserts a new row (no existing claim) or
updates an existing row only if its lease has expired (reclaim) or it
already belongs to this `holder_id` (renew).

The exact column types, indexes, migration, and acquire/renew/release SQL
are future implementation scope (M9.7+), not M9.6A.

## 8. Integration with `advance()`

Future `advance(...)` sequence under the recommended strategy (compare with
the current sequence in section 2):

1. **Duplicate `MessageSid` check** (new, first, outside any customer claim):
   look up `(tenant_id, message_sid)`. If already processed, return the
   recorded result for that message without touching the customer claim at
   all. *(Today this check is implicit inside `append_turn_if_new`'s unique
   constraint, which runs after routing; the future design hoists an
   equivalent check to before claim acquisition so retried webhook deliveries
   are cheap and customer-claim-free.)*
2. **Acquire customer claim** for
   `conversation_customer_key(tenant_id, customer_phone)`. Busy/timeout
   handling per section 10.
3. **Route/open session**: `get_latest_session_for_customer`,
   `get_or_create_open_session` - under the held claim.
4. **Append turn**: `append_turn_if_new` - under the held claim.
5. **Branch** (as today): `draft_created` short-circuit, orphan-draft
   recovery (`get_order_by_conversation_id` + `mark_draft_created`), or
   proceed to parsing.
6. **Parser, outside any DB transaction, inside the logical claim**:
   `list_turns`, `list_products`, then `parse(...)`.
7. **Revalidation** (short transaction, after parser returns): re-read the
   session this lifecycle intends to write to; confirm it is still the
   customer's routable session and in the expected state.
   * If revalidation fails (state changed underneath - should be rare while
     holding the claim, but possible after lease expiry/reclaim): abort this
     lifecycle's draft-creation path, record an outcome reflecting the
     conflict, and release the claim. The next inbound message (or a retry)
     re-routes against current state.
8. **Commit draft/session outcome**: `create_draft(...)` internals,
   `mark_draft_created(...)` - under the held claim, immediately after
   successful revalidation.
9. **Record advancement outcome**: `record_advancement_attempt(...)` - within
   or immediately after the lifecycle. Failure semantics:
   * If `record_advancement_attempt(...)` itself fails (as today, it is
     wrapped and logged rather than raised -
     `ConversationAdvancementService._record_outcome`), the lifecycle still
     releases its claim; the session/draft outcome already committed in
     step 8 is not rolled back, but the observation read may show a stale
     `latest_advancement_outcome` until a future attempt for this customer
     updates it. This matches today's "best-effort observability" semantics
     and is preserved, not strengthened, in M9.6A.
10. **Release claim** (always - success, revalidation-abort, or error path).
    If the process dies before this step, lease expiry handles recovery
    (section 10).

## 9. Idle expiry as first consumer

Runtime idle-boundary expiry (`docs/M9_4E_IDLE_BOUNDARY_DESIGN.md`) becomes
the first consumer of this unit of work, and becomes safe under the
recommended strategy as follows:

* The idle-boundary transition - "this customer's `open` session is idle;
  open a new session for the next inbound message" - is itself a
  state-changing decision for `(tenant_id, customer_phone)`, so it must
  **also** acquire that customer's claim before running, exactly like
  `advance(...)`'s own routing step.
* Because both "expire old session / open new session" and "complete this
  customer's in-flight draft" (`create_draft` -> `mark_draft_created` for the
  *old* session) require the **same** customer claim, they cannot interleave:
  whichever lifecycle acquires the claim first runs to completion (release)
  before the other can proceed. **Expire-old + create-new cannot race
  draft-completion**, because both serialize on
  `conversation_customer_key(tenant_id, customer_phone)`.
* Concretely, this closes the M9.4E xfail scenario: if `old`'s `advance(...)`
  lifecycle is mid-flight (holding the claim, about to call
  `mark_draft_created`), an idle-boundary check for the same customer cannot
  acquire the claim to open `new` until `old`'s lifecycle releases it -
  by which point `old` is `draft_created` and idle-boundary logic can observe
  that and decide not to open `new` at all (or, if it still does for a
  genuinely later message, `get_latest_session_for_customer` must prefer the
  `draft_created` session per the existing invariant in
  `docs/M9_4E_IDLE_BOUNDARY_DESIGN.md` section 2).
* **The existing strict xfail test is the future acceptance test to flip
  green**:
  `tests/test_conversation_state_store.py::test_draft_created_session_remains_latest_over_later_open_session_for_customer`.
  A future implementation must make this pass (by satisfying the section-2
  invariant - either `get_latest_session_for_customer` prefers
  `draft_created`, or the competing `open` session is never created) and then
  remove the `xfail` marker; the test is `strict=True` so an unexpected pass
  (XPASS) would fail the suite first, forcing the marker removal to be
  deliberate.
* **Additional test needed to prove the race is closed**: a concurrency test
  that interleaves (a) an in-flight `advance(...)` for a customer that is
  about to call `mark_draft_created`, with (b) a concurrent idle-boundary
  expiry attempt for the *same* `(tenant_id, customer_phone)` that would open
  a new session - and asserts that the claim mechanism serializes them (one
  completes before the other's claim acquisition succeeds), and that the
  final state satisfies the M9.4E section-2 invariant regardless of
  interleaving order. A second test should assert that the same two
  operations for **different** customers proceed concurrently without
  blocking (the "different customers must not block" invariant from
  section 4).

## 10. Retry/error semantics

* **Claim acquisition timeout / busy behavior**: if a customer's claim is
  already held (live lease) when a new lifecycle attempts to acquire it, the
  new lifecycle either (a) retries acquisition with a short bounded
  backoff/timeout, or (b) returns immediately with a "deferred, try again"
  outcome that does not append a turn or record an advancement outcome yet.
  Either way, the duplicate-`MessageSid` gate (step 1 of section 8) ensures a
  webhook retry of the *same* message is cheap; a genuinely new message
  arriving while the customer's claim is held is the expected steady-state
  case this design serializes, not an error.
* **Parser error while claim held**: unchanged from today's
  `ConversationAdvancementOutcome.TURN_APPENDED_INCOMPLETE` /
  `ParserError` handling - the turn was already appended (step 4, under the
  claim), the parser error is caught, the lifecycle records that outcome
  (step 9) and releases the claim (step 10) normally. No special claim
  handling beyond "always release on the way out, including error paths."
* **Process crash / connection loss while claim held**: the claim row remains
  with its `lease_expires_at` from acquisition/last renewal. No other
  lifecycle for this customer can acquire the claim until that lease expires.
  Once expired, a later attempt (the next inbound message, or a retried idle
  check) may reclaim the claim (section 7's "insert-or-update if expired").
  Any partial writes the crashed lifecycle made before crashing (e.g., a turn
  was appended but `mark_draft_created` never ran) are handled by the normal
  `advance(...)` flow on the next attempt: `append_turn_if_new` is already
  idempotent on `message_sid`, and orphan-draft recovery
  (`_recover_orphan_draft`/`_recover_from_create_draft_conflict`) already
  exists for "a draft exists but the session wasn't marked" - these existing
  mechanisms are the recovery path, not a new one.
* **Lease expiry and safe retry**: lease expiry is the only recovery
  mechanism for an abandoned claim - there is no separate "are you still
  alive" check. The lease duration must be chosen long enough to cover the
  worst-case parser/LLM latency plus the short post-parse commit window, so
  that a *live*, slow lifecycle is not reclaimed out from under itself
  (a renew step, per section 6, extends the lease if the lifecycle is still
  progressing).
* **Revalidation failure after parser**: if step 7 (section 8) finds the
  session no longer matches expectations (e.g., it was reclaimed and advanced
  by a later attempt after this lifecycle's lease expired), this lifecycle
  must **not** proceed to `create_draft`/`mark_draft_created` against stale
  state. It records an outcome reflecting the conflict (a new or repurposed
  `ConversationAdvancementOutcome` value is future implementation scope) and
  releases its claim without writing draft/session state. The
  already-appended turn (step 4) remains - it is real customer input - but no
  draft is created from this lifecycle's parse result; a subsequent attempt
  (own retry or next inbound message) will see the up-to-date session and
  transcript (including this lifecycle's appended turn) and proceed normally.
* **Duplicate `MessageSid` retry behavior**: unchanged in spirit from today -
  `append_turn_if_new`'s existing `IntegrityError`-on-unique-constraint path
  already returns the existing turn with `appended=False`. The future design
  hoists an equivalent check earlier (step 1, section 8) so that a retried
  webhook for a `message_sid` that was already fully processed (including
  outcome recorded) can return the previously-recorded result without
  acquiring the customer claim at all - reducing claim contention for retried
  deliveries, which are common with Twilio webhooks.
* **Observability outcome when advancement is blocked/deferred by lock/claim
  contention**: `record_advancement_attempt(...)` continues to be the
  mechanism by which `latest_advancement_outcome` /
  `latest_parse_error_category` are surfaced to M9.5's observation reads
  (required invariant, section 4). A lifecycle that is deferred *before*
  acquiring the claim (busy, per "claim acquisition timeout" above) has not
  yet appended a turn or reached a terminal outcome for *this* message, so it
  does not call `record_advancement_attempt` for this attempt - the customer's
  `latest_advancement_outcome` remains whatever it was from their last
  completed lifecycle until a retry succeeds. A lifecycle that fails
  *after* acquiring the claim (parser error, revalidation failure) does call
  `record_advancement_attempt` with an outcome reflecting that failure, per
  the existing pattern, so the operator-visible state always reflects the
  most recent *completed* lifecycle for that customer.

## 11. Design-conformance checklist for future M9.7 implementation

* [ ] Duplicate `MessageSid` gate runs before customer-claim acquisition.
* [ ] All state-changing lifecycle decisions for a customer
  (`_route_session`, `append_turn_if_new`, draft creation/marking, idle
  expiry) are serialized by `conversation_customer_key(tenant_id,
  customer_phone)`.
* [ ] Different customers (different `conversation_customer_key`) proceed
  fully concurrently - no shared lock across customers.
* [ ] The parser/LLM call runs with no open DB transaction/connection held by
  the lifecycle.
* [ ] The parser/LLM call runs while the lifecycle's customer claim is held
  (logically, not via a DB transaction).
* [ ] Session state is revalidated under a short transaction after the parser
  returns and before `create_draft`/`mark_draft_created` commit.
* [ ] The M9.4E idle-boundary race is closed: expire-old and
  create-draft-for-old cannot both succeed in conflicting ways for the same
  customer (the section-9 concurrency test passes).
* [ ] No broad `StorageInterface` change is introduced unless explicitly
  accepted as part of that milestone's scope (the claim table is expected to
  be accessed via a small dedicated store, not folded into
  `StorageInterface`).
* [ ] No direct page/UI mutation path is introduced - claim
  acquisition/release is internal to service-layer lifecycle code, not
  exposed to Streamlit pages.
* [ ] Observability is preserved: `record_advancement_attempt(...)` continues
  to run for every lifecycle that reaches a terminal outcome (success,
  parser error, revalidation failure), per section 10.
* [ ] `tests/test_conversation_state_store.py::test_draft_created_session_remains_latest_over_later_open_session_for_customer`
  flips from `strict=True` xfail to passing, and the marker is removed.

## 12. Acceptance tests for future implementation

Documented here for future implementation; **not implemented in M9.6A**:

* `test_draft_created_session_remains_latest_over_later_open_session_for_customer`
  (existing xfail) flips to passing once the marker is removed.
* A same-customer concurrency test: two `advance(...)`-shaped lifecycles for
  the same `(tenant_id, customer_phone)` started concurrently must serialize
  - the second does not begin its routing/append/parse steps until the first
  releases its claim, and the final state is consistent with exactly one
  lifecycle having run at a time.
* A different-customers concurrency test: two lifecycles for different
  `(tenant_id, customer_phone)` pairs (including same `tenant_id`, different
  `customer_phone`) proceed without either blocking on the other's claim.
* A duplicate-`MessageSid` short-circuit test: a retried `advance(...)` call
  with a `message_sid` that was already fully processed returns the prior
  result without acquiring (or contending for) the customer claim - verified
  e.g. by asserting the claim table is untouched by the retry.
* A parser-error-releases-claim test: a lifecycle whose `parse(...)` raises
  `ParserError` still releases its customer claim (verifiable by a subsequent
  lifecycle for the same customer acquiring it without waiting for lease
  expiry).
* A lease-expiry/recovery test: a claim with an expired lease (simulating a
  crashed holder) can be reclaimed by a new lifecycle, which then completes
  normally.
* A revalidation-catches-staleness test: after a lifecycle's parser call
  returns, the session it intended to write to has been changed by a
  (simulated) reclaiming lifecycle; the original lifecycle's revalidation
  step detects this, does not write a draft, and records a conflict outcome
  instead.
* An idle-expiry-vs-draft-completion interleaving test (section 9): both
  possible orderings of "idle expiry attempts to open `new`" vs "in-flight
  `advance` completes `old`'s draft" for the same customer are exercised, and
  both end in a state satisfying the M9.4E section-2 invariant (at most one
  routable session, `draft_created` wins "latest" if both occurred).

## 13. Explicit non-goals

The following are explicitly **not** part of M9.6A:

* No runtime implementation of any of the above - this document is the
  deliverable.
* No migration (no `conversation_customer_claims`/`conversation_customer_locks`
  table is added).
* No `StorageInterface` change.
* No draft amendment.
* No outbound replies.
* No payment flow.
* No parser prompt change.
* `live_sheets` was not run.
* No advisory-lock validation spike (the pre-flight's proposed spike is
  explicitly not run in this slice).

## 14. M9.6B validation result

M9.6B added `tests/test_conversation_customer_claim_spike.py`, a
`live_postgres`-only validation spike for the durable per-customer
claim/lock row recommended in section 6 and sketched in section 7. The
spike is not a runtime implementation: it creates and drops a test-only
`conversation_customer_claims_spike` table directly via SQL (not
Alembic-managed, not part of `Base.metadata`), and its `acquire_claim(...)`
/ `release_claim(...)` helpers are test-local functions, not a production
store or `StorageInterface` change.

Against real Postgres, the spike proved:

* **Same customer serializes**: two concurrent workers contending for the
  same `(tenant_id, customer_key)` claim - Worker B's `acquire_claim(...)`
  returns `False` (no row matches `RETURNING`) while Worker A's lease is
  live, and only succeeds after Worker A releases. Ordering is proven via a
  recorded event sequence (`a_acquired` -> `b_blocked` -> `a_released` ->
  `b_acquired`) coordinated with `threading.Event`, not sleeps alone.
* **Different customers do not block each other**: Worker A holds a claim
  for customer A indefinitely; Worker B acquires a claim for customer B
  (same `tenant_id`) immediately, without waiting.
* **Lease/recovery**: a claim seeded with an already-expired
  `lease_expires_at` (simulating a crashed holder) can be taken over by a
  new holder via the same `acquire_claim(...)` upsert; a live (non-expired)
  claim cannot be taken over - `acquire_claim(...)` returns `False` and the
  row is unchanged.
* **Parser delay outside any DB transaction**: `acquire_claim(...)` and
  `release_claim(...)` are each exactly one short `engine.begin()`
  transaction (`INSERT ... ON CONFLICT (tenant_id, customer_key) DO UPDATE
  ... WHERE lease_expires_at <= :now RETURNING holder_id`); between acquire
  and release, `engine.pool.checkedout() == 0` during a simulated
  parser/LLM delay, confirming the claim survives as committed row state
  with no held connection/transaction.

All 4 tests pass under
`pytest tests/test_conversation_customer_claim_spike.py -q -m
live_postgres`. This validates the primitive recommended in sections 6 and
7 against real Postgres behavior; it does not implement, migrate, or wire
the primitive into `advance(...)` - that remains M9.7+ scope per section
11's conformance checklist.
