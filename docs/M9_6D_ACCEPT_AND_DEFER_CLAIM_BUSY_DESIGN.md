# M9.6D-fix Accept-and-Defer Design for Claim-Busy

Status: design only. No runtime behavior, migration, or `StorageInterface`
change is implemented in this slice.

Baseline: `ed31030` (`feat(web): serialize conversation advancement by
customer claim`)

M9.6D-fix replaces the M9.6D claim-busy strategy
(`try_acquire` fails -> `HTTP 503`, relying on Twilio redelivery to
eventually process the message) with **accept-and-defer**: on claim-busy,
the webhook durably persists the raw inbound message, returns `HTTP 202`
immediately, and Duna itself - not Twilio - drives reprocessing once the
customer's claim frees up. This document records the schema, write
behavior, drain trigger, ordering guarantee, idempotency proof,
reprocessing path, scope boundary, migration note, and `StorageInterface`
boundary for that replacement. No runtime code, migration, or test changes
are made in M9.6D-fix. This document is the artifact.

## 0. Why this design exists: the M9.6D claim-busy live-smoke finding

A live smoke of the M9.6D claim-busy-via-`503` strategy was run against
baseline `ed31030` (`POST /webhooks/twilio/whatsapp`, real Twilio WhatsApp
sandbox, throwaway Neon branch, cloudflared tunnel). Full evidence is
recorded in the `DECISIONS.md` entry for this slice; the load-bearing facts
for this design are:

* A `conversation_customer_claims` row was manually inserted for
  `customer_key = +573223454241` (the value of
  `normalize_customer_claim_key(tenant_id, customer_phone)` for the sandbox
  sender - **not** `whatsapp:+573223454241`, the raw Twilio `From` value)
  with a `lease_expires_at` far enough in the future to hold the claim busy
  for the duration of the smoke.
* A real WhatsApp message was then sent from the joined sandbox number.
  `try_acquire` correctly returned `False` (busy); the webhook returned
  `HTTP 503`; Uvicorn logged
  `POST /webhooks/twilio/whatsapp HTTP/1.1" 503 Service Unavailable`.
* Twilio's Request Inspector recorded this delivery with HTTP status `503`
  and warning `11200` for `MessageSid = SMea149d267f55a8183b3452883b140abb`.
* `processed_messages` had **zero rows** for that `MessageSid`
  (`sid_rows = 0`) - confirming the M9.6D claim-before-dedup ordering worked
  exactly as designed: a busy claim returns `503` *before*
  `try_record_message(...)`, so the `MessageSid` was never recorded.
* The first `503` was logged at approximately `2026-06-12 19:01:22 UTC`. The
  manually-held claim was left in place until approximately
  `2026-06-12 19:29:12 UTC` (~28 minutes later).
* In that ~28-minute window, **no redelivery of `MessageSid
  SMea149d267f55a8183b3452883b140abb` reached Uvicorn**. The message was
  never processed; no `processed_messages`, `conversation_turns`, or
  `orders` row was ever created for it.

**Conclusion**: claim-before-dedup correctly avoided the "recorded-then-lost"
hazard M9.6D was designed to fix, but the *other* half of the claim-busy
contract - "Twilio retries a `503` within a useful window, so the message is
eventually processed" - did not hold in this environment. A strategy that
depends on that retry to avoid permanent message loss is therefore **not
proven safe** and must be treated as failed, not closed. A Twilio fallback
URL was considered and rejected: a fallback URL would route to the same
backend and the same per-customer claim, and would fail the same way (still
busy) - it does not change the outcome.

**Replacement**: accept-and-defer. The webhook never again depends on Twilio
redelivery for a message that hits claim-busy. Instead:

1. On claim-busy, the webhook persists the raw inbound message to a new
   `deferred_inbound` table (section 1) via an idempotent insert
   (section 2), and returns `HTTP 202` - not `503` - so Twilio considers the
   delivery successful and does not retry.
2. The `MessageSid` is never lost: it lives in `deferred_inbound` until
   Duna's own drain logic processes it.
3. When the customer's claim frees, Duna drains pending `deferred_inbound`
   rows for that `(tenant_id, customer_key)` and reprocesses each through
   the normal advancement path (section 6), in `received_at` order
   (section 4).

## 1. `deferred_inbound` schema

### Columns

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `message_sid` | `String(ID_LENGTH)` (80) | no | **Primary key.** Twilio `MessageSid`, e.g. `SMea149d267f55a8183b3452883b140abb`. |
| `tenant_id` | `String(TENANT_ID_LENGTH)` (120) | no | Same value the webhook would have passed to `try_record_message`/`advance`. |
| `customer_key` | `String(PHONE_LENGTH)` (80) | no | `normalize_customer_claim_key(tenant_id, customer_phone)`, computed once at defer time. The drain query's key; equals `conversation_customer_claims.customer_key` for this tenant/customer. |
| `from_number` | `String(PHONE_LENGTH)` (80) | yes | Raw Twilio `From` header (e.g. `whatsapp:+573223454241`), mirroring `ProcessedMessageRow.from_number`. |
| `raw_body` | `Text` | yes | Raw Twilio `Body`, mirroring `ProcessedMessageRow.raw_body`. |
| `received_at` | `DateTime(timezone=True)` | no | Captured by the webhook (`utc_now()`) at the same point `ProcessedMessageRow.received_at` would be - i.e. *before* the claim-busy check, so it reflects when Twilio's request actually arrived. |
| `deferred_at` | `DateTime(timezone=True)` | no | `default=utc_now`. When this row was written to `deferred_inbound`. Equal to `received_at` on the first defer-write; only diverges if a later request re-defers the same `message_sid` (section 4's ahead-of-queue check) - in which case `deferred_at` is **not** updated by the later attempt (the `ON CONFLICT DO NOTHING` insert is a true no-op; see section 2). |
| `processed_at` | `DateTime(timezone=True)` | yes | `NULL` until the drain successfully processes this row (section 5). The drain query's primary filter is `processed_at IS NULL`. |
| `processing_started_at` | `DateTime(timezone=True)` | yes | Set at the start of each drain attempt for this row; left as-is on success (superseded by `processed_at`) or on a clean "still busy" outcome. Used by the sweep backstop to detect a stale in-flight attempt (section 3). |
| `attempt_count` | `Integer` | no | `default=0`. Incremented at the start of each drain attempt. Observability/staleness signal only - not required for correctness (section 3, section 5). |

### Why `message_sid` alone is the unique key (not `tenant_id + message_sid`)

`processed_messages.message_sid` is already the **global** primary key for
the same artifact - an inbound Twilio message - with no `tenant_id`
component (`ProcessedMessageRow`, `src/duna_orders/storage/postgres_models.py:287-304`).
Twilio's `MessageSid` is allocated from Twilio's own global identifier
namespace (format `SM` + 32 hex characters), not scoped per Duna tenant; the
existing `processed_messages` table already relies on this global uniqueness
with no `tenant_id` qualifier.

`deferred_inbound` rows are deferred copies of the *same* artifact that
`processed_messages` rows represent once processed. Introducing a different
uniqueness scope (`tenant_id + message_sid`) for the same identifier in a
sibling table would be an unjustified divergence: it would only matter if
the same `message_sid` could legitimately belong to two different tenants,
which `processed_messages`'s existing schema already assumes cannot happen.
**`message_sid` alone, as the primary key, is correct and consistent with
the existing precedent.**

### Indexes

1. **Primary key on `message_sid`** - satisfies the "unique constraint on
   `message_sid`" requirement directly and is the idempotency anchor for
   section 2's defer-write.

2. **`ix_deferred_inbound_pending_by_customer`** - a partial index on
   `(tenant_id, customer_key, received_at, deferred_at, message_sid)` `WHERE
   processed_at IS NULL`. Postgres partial indexes are an existing pattern in
   this codebase (`ConversationSessionRow`'s
   `uq_conversation_sessions_one_open_per_customer`,
   `postgresql_where=(status == "open")`,
   `src/duna_orders/storage/postgres_models.py:378-386`). This single index
   serves three queries:
   * `has_pending(tenant_id, customer_key)` (section 4) - an existence check
     against the index, no table access.
   * `list_pending_for_customer(tenant_id, customer_key)` (section 6) - an
     ordered range scan already sorted `received_at, deferred_at,
     message_sid` (section 4's ordering), no separate sort step.
   * the sweep backstop's "which `(tenant_id, customer_key)` pairs have
     pending work" query (section 3) - an index-only scan over
     `(tenant_id, customer_key)` with `DISTINCT`.

   Because this index is partial (`processed_at IS NULL`), it stays tiny in
   steady state - the overwhelmingly common case is zero pending rows for
   any given customer - which keeps `has_pending` cheap even though it runs
   on the hot path (section 4).

3. **No additional index is required for the MVP.** A
   tenant-wide `(tenant_id, deferred_at)` index for an operator-facing "how
   many messages are currently deferred for tenant X" view is plausible
   future observability, but nothing in sections 2-6 needs it; it is left as
   an optional addition for M9.6D-fix-impl to add only if an operator view
   is built at the same time.

### Error/attempt metadata: what is included and why

`attempt_count` and `processing_started_at` are included because they are
cheap (one integer, one nullable timestamp) and because the sweep backstop
(section 3) needs *some* signal to distinguish "never attempted" from
"attempted but the attempt never completed" without relying on a separate
worker registry. Per-attempt `last_error_message`/`last_error_code` columns
(à la `OutboundMessageRow`) are **not** included: reprocessing is fully
idempotent (section 5), so a failed/incomplete attempt is always safely
retried by the next trigger, and the existing application logs already
capture the exception. If operational experience after M9.6D-fix-impl shows
a need for structured error capture on `deferred_inbound`, that is an
additive column change via a later migration, not a blocker here.

## 2. Claim-busy write behavior (idempotent defer-write, `202`)

When `claim_store.try_acquire(...)` returns `False` (claim busy), the
webhook:

1. Calls `deferred_inbound_store.try_defer_message(message_sid=...,
   tenant_id=..., customer_key=..., from_number=raw_sender,
   raw_body=inbound_body, received_at=<utc_now() captured at the top of the
   handler, same point ProcessedMessageRow.received_at would use>)`.
2. Returns `Response(status_code=202)`.

`try_defer_message` is `INSERT INTO deferred_inbound (...) VALUES (...) ON
CONFLICT (message_sid) DO NOTHING`, mirroring the insert-first idempotency
pattern `PostgresProcessedMessageStore.try_record_message` already uses
(`src/duna_orders/storage/processed_messages.py`) - the only difference is
`ON CONFLICT DO NOTHING` instead of catching `IntegrityError`, since here a
conflict is an expected, harmless steady state rather than an error. It
returns a `bool` (`True` if this call inserted a new row, `False` if a row
for this `message_sid` already existed) for logging only.

**The webhook returns `202` in both cases.** Whether this is the first
defer-write for this `MessageSid` or a repeat (e.g. Twilio retried the
original request while the claim was still busy, or this request re-deferred
itself via section 4's ahead-of-queue check), the postcondition is the same:
*this message is durably queued for processing*. That postcondition was
already true before this call if the row existed, so `202` is correct either
way.

**`processed_messages` is not written on this path.** `try_record_message`
is only ever called *after* a successful `try_acquire` (unchanged
claim-before-dedup ordering from M9.6D); when `try_acquire` returns `False`,
`try_record_message` is never reached. This is precisely the live-smoke
finding: `sid_rows = 0` in `processed_messages` for the claim-busy
`MessageSid`. Under accept-and-defer, that SID instead gets exactly one row
in `deferred_inbound`, and `processed_messages` remains empty for it until
the drain (section 6) processes it successfully.

`202 Accepted` (rather than `200`) is chosen deliberately for log/operator
clarity - "accepted for processing, not yet processed" - though Twilio's
webhook contract treats any `2xx` identically (delivered, no retry
expected/needed).

## 3. Drain trigger

### Options considered

* **Periodic sweep only.** A scheduled job scans `deferred_inbound` for
  pending rows and drains them. Simple, but introduces latency proportional
  to the sweep interval for *every* deferred message, even the common case
  (claim frees within milliseconds of the original holder finishing).
* **Drain-on-release only.** When the active claim holder finishes
  `advance()` and releases, it checks `deferred_inbound` for that
  `(tenant_id, customer_key)` and drains any pending rows before/while
  responding. Near-zero added latency for the common case, but has no
  recovery path if the triggering release never happens (process crash,
  unhandled exception before the trigger runs, or the customer simply never
  sends another message after the one that's now stuck pending).
* **Drain-on-release + light sweep backstop (recommended).** Drain-on-release
  handles the common case with minimal latency; a separate, infrequent sweep
  reclaims anything drain-on-release missed.

### Recommendation: drain-on-release (FastAPI `BackgroundTasks`) + sweep script backstop

**Drain-on-release** is implemented as a `starlette.background.BackgroundTask`
attached to the webhook's own `Response`. `BackgroundTasks` are part of the
ASGI app already running this webhook - they execute in-process, on the same
event loop, *after* the response has been sent to Twilio. This is not a new
process, queue, or scheduler: it is additional work the same request handler
schedules for itself, using a mechanism FastAPI/Starlette already ships.
**This satisfies the hard stop**: drain-on-release can be expressed without a
separate worker/scheduler.

Concretely (see section 6 for the exact call shape): whenever the webhook's
shared internal processing function acquires-and-releases the customer claim
(i.e. the outcome is `PROCESSED` or `DUPLICATE` - any outcome where this
request actually held the claim), the webhook attaches a background task that
calls `_drain_deferred_for_customer(tenant_id, customer_key)`. That helper
loops: fetch the next pending row (section 1's ordered index), attempt to
process it via the *same* shared internal function (which acquires its own
claim), mark it processed on a terminal outcome (section 5), and stop the
loop the moment a drain attempt itself reports claim-busy (the remaining
rows wait for the next trigger - they are never dropped).

Because the loop is internal to one background task invocation, no recursive
task-scheduling is needed: it already handles every pending row for this
customer in one pass per trigger.

### Sub-answers

**What happens if the drainer crashes mid-drain?**

Two crash shapes:

* *Whole-process crash* (Uvicorn killed mid-`advance()`): identical to any
  other in-flight `advance()` crash today - the customer claim's lease
  (`DEFAULT_CLAIM_LEASE_DURATION = 60s`) expires and `try_acquire`'s `WHERE
  lease_expires_at <= now()` takeover (existing M9.6C/M9.6D behavior,
  unchanged) lets a future attempt re-acquire it. `append_turn_if_new`
  idempotency and orphan-draft recovery (existing M9.6 machinery) handle any
  partially-written `advance()` state exactly as they do today. The
  `deferred_inbound` row stays `processed_at IS NULL`, possibly with a stale
  `processing_started_at`.
* *In-loop exception for one row* (not a process crash): caught, logged, row
  left `processed_at IS NULL` (not marked processed), loop ends for this
  trigger. The row is retried by the next trigger.

**How is an un-drained row reclaimed?**

Two reclaim paths, in order of how often they fire:

1. *The next inbound message from the same customer*, if there is one,
   triggers drain-on-release again naturally (the row is still `processed_at
   IS NULL`), and section 4's ahead-of-queue check additionally ensures that
   message itself joins the queue behind it rather than overtaking it. Most
   reclaim happens this way, for free.
2. *The sweep backstop* (a standalone script, see below) periodically finds
   `(tenant_id, customer_key)` pairs with `processed_at IS NULL AND
   (processing_started_at IS NULL OR processing_started_at < now() -
   RECLAIM_THRESHOLD)` and calls `_drain_deferred_for_customer` for each.
   This is the only path that covers a customer who never messages again
   after triggering a deferral.

**Does a row need `processing_started_at` / `processing_holder_id` /
`attempt_count`?**

* `processing_started_at`: **yes** - the cheapest possible "is this row
  currently/recently being worked, or definitely idle" signal for the sweep,
  without a separate worker registry.
* `processing_holder_id`: **no**. Per-attempt holder identity is already
  provided at the *claim* layer (`conversation_customer_claims.holder_id`,
  generated fresh per `_process_validated_inbound_message` call). A second
  holder-id on `deferred_inbound` would duplicate that without adding
  information, because correctness never depends on it - reprocessing is
  idempotent regardless of which attempt's holder ultimately succeeds
  (section 5).
* `attempt_count`: **included as observability only** (section 1). Because
  reprocessing is fully idempotent, a sweep can safely re-attempt *any*
  `processed_at IS NULL` row without consulting `attempt_count` - it exists
  so an operator can notice "this row has been retried 50 times" as a signal
  something else is wrong (e.g. a perpetually-stuck claim holder), not
  because the drain loop's correctness depends on it.

**What is the minimum viable version for M9.6D-fix-impl?**

* `deferred_inbound` table (new migration, section 9) with the columns and
  one partial index from section 1.
* `PostgresDeferredInboundStore` (new narrow store, section 10):
  `try_defer_message`, `has_pending`, `list_pending_for_customer`,
  `mark_processed`, `mark_attempt`, and a sweep-only
  `list_stale_pending_customers(reclaim_threshold)`.
* The shared internal function extracted from the webhook handler
  (section 6), with its four outcomes (`PROCESSED`, `DUPLICATE`,
  `CLAIM_BUSY`, `PENDING_AHEAD`).
* Webhook: claim-busy and `PENDING_AHEAD` -> defer-write + `202`;
  `PROCESSED`/`DUPLICATE` -> schedule `_drain_deferred_for_customer` as a
  `BackgroundTask`, return `200` as today.
* `_drain_deferred_for_customer`: the loop described above.
* A standalone `scripts/drain_deferred_inbound.py` sweep script (see below)
  as the crash-recovery backstop.
* Live re-smoke per `docs/SMOKE_CLAIM_BUSY_ACCEPT_AND_DEFER.md` (section
  "future accept-and-defer verification").

**Can the design stay out of a general queue/worker architecture?**

Yes. Drain-on-release is in-process (`BackgroundTasks`, same ASGI app, same
event loop, no new process/queue). The sweep backstop is a standalone script
following the existing `scripts/*.py` convention (e.g.
`scripts/smoke_preflight.py`) - it constructs the app's storage/services the
same way those scripts already do, and is invoked by an *external* scheduler
(OS-level cron / hosting-provider cron), which is operational configuration,
not new application architecture. There is no `Job` table, no `SKIP LOCKED`
multi-consumer queue, and no generic retry/backoff framework - `attempt_count`
is an observability counter, not a backoff policy. `deferred_inbound` itself
is read/written through narrow store methods exactly like
`processed_messages` / `conversation_customer_claims` / `outbound_messages`.

## 4. Ordering

### Defined order

Pending rows for a `(tenant_id, customer_key)` are processed in:

```
ORDER BY received_at ASC, deferred_at ASC, message_sid ASC
```

`received_at` is the primary key (captured by the webhook before the
claim-busy check, reflecting Twilio's actual delivery order for this
customer). `deferred_at` is a tie-break for the - extremely unlikely - case
of identical `received_at` timestamps. `message_sid` is the final
deterministic tie-break, guaranteeing a total order even in a microsecond
tie.

### Why order matters for conversation correctness

`ConversationAdvancementService._advance_open_session` renders the
transcript from `list_turns(...)` in `sequence_number` order
(`_render_transcript`, `src/duna_orders/services/conversation_advancement.py:358-362`),
and `sequence_number` is assigned by `append_turn_if_new(...)` **at the time
each turn is appended**. If a later message (e.g. "Actually make that 3") is
appended *before* an earlier message (e.g. "I want 2 arepas"), the transcript
handed to the parser/LLM would present them in the wrong order - changing
how a follow-up correction is interpreted, potentially producing a wrong
draft order. Preserving `received_at` order at append time is therefore a
correctness requirement, not just a nicety.

### How the design prevents later text from overtaking earlier text

Two mechanisms work together:

1. **Serialized draining.** `_drain_deferred_for_customer` processes pending
   rows for one customer **one at a time**, in the order above. Each row's
   reprocessing acquires the *same* per-customer claim (section 6), so
   message B's `advance()` cannot append its turn until message A's
   `advance()` has fully completed and released the claim. This guarantees
   `conversation_turns.sequence_number` reflects `received_at` order for
   every row that goes through `deferred_inbound`.

2. **Ahead-of-queue check (closes a live/deferred race).** Consider: message
   A arrives, claim busy (some other lifecycle H is mid-`advance()` for an
   *earlier* message from the same customer), A is deferred,
   webhook returns `202`. Now message B (sent after A) arrives. By the time
   B's webhook request runs `try_acquire`, has H released? If H is still
   working, B *also* hits claim-busy and is deferred via the same path - no
   problem, both A and B drain in order. But if H has just released, and
   A's drain-on-release background task has not yet run `try_acquire`
   (a real, if narrow, race in a cooperative event loop), B's request could
   acquire the now-free claim and process **live**, appending its turn
   *before* A's deferred turn is appended - exactly the "later text
   overtakes earlier text" failure.

   To close this, the shared internal function's first step **after** a
   successful `try_acquire` (and before `try_record_message`) is:
   `deferred_inbound_store.has_pending(tenant_id, customer_key)`. If `True`,
   this message - even though it just acquired the claim live - is itself
   deferred (the same idempotent `try_defer_message` insert from section 2)
   and the claim is released (the existing `try/finally` already releases on
   every exit path) without calling `try_record_message`/`advance`. The
   webhook returns `202`. This release then triggers
   `_drain_deferred_for_customer`, which now drains **both** A and B (and any
   others) in `received_at` order.

   This check is one indexed read against the same partial index that serves
   the drain query (section 1), executed only on the already-claim-holding
   path (claim-busy responses already return immediately and never reach
   this check). In steady state the partial index has zero rows for almost
   every customer, so the cost is one cheap index probe per non-busy webhook
   request - the price of the strict ordering guarantee.

With both mechanisms, **no message for a customer with any pending
`deferred_inbound` row can be processed live ahead of that row** - it either
finds the claim busy (deferred directly) or finds the claim free but
observes a pending row (defers itself via the ahead-of-queue check). Either
way it joins the queue and is drained in `received_at` order.

## 5. Idempotency proof

1. **A claim-busy-deferred `MessageSid` is not yet in `processed_messages`.**
   `try_record_message` is only called after a successful `try_acquire`
   (unchanged from M9.6D). When `try_acquire` returns `False`,
   `try_record_message` is never reached for this `message_sid` - this is
   exactly the live-smoke observation (`sid_rows = 0`).

2. **Deferred reprocessing goes through the normal dedup gate.** The drain
   loop calls the *same* shared internal function the live webhook path uses
   (section 6). Its first DB-mutating step after claim acquisition (and after
   the ahead-of-queue check, which a drain-initiated call always passes -
   draining *is* the pending work) is `try_record_message(...)`.

3. **`try_record_message` records it exactly once.**
   `PostgresProcessedMessageStore.try_record_message` inserts with
   `message_sid` as primary key; a second insert for the same `message_sid`
   raises `IntegrityError`, caught, returns `False` (existing code,
   unchanged). Regardless of how many times the drain loop is invoked for the
   same row, `processed_messages` ends up with exactly one row for that
   `message_sid`.

4. **Repeated/crashed drains do not double-process.** Suppose the shared
   function fully succeeds for row X (`advance()` ran,
   `mark_order_created` if applicable) but the process crashes *before*
   `deferred_inbound_store.mark_processed(X)` runs. The row stays
   `processed_at IS NULL`. The next drain attempt calls the shared function
   again for X: `try_acquire` succeeds, `has_pending` is irrelevant (X is the
   row being drained), `try_record_message(X)` now returns `False` (already
   recorded by the first attempt) -> the shared function takes the
   `DUPLICATE` branch (no `advance()` call, claim released). **Both
   `PROCESSED` and `DUPLICATE` are "safe to mark processed" outcomes** -
   in both cases `processed_messages` durably contains `X` by the time the
   shared function returns. The drain loop calls `mark_processed(X)` for
   either outcome. The **only** outcome that leaves the row pending is
   `CLAIM_BUSY` (the attempt could not even get the claim this time) - and
   `PENDING_AHEAD` cannot occur for a row that is itself the head of the
   pending queue.

5. **`deferred_inbound`'s unique `message_sid` prevents duplicate deferred
   rows.** `try_defer_message`'s `INSERT ... ON CONFLICT (message_sid) DO
   NOTHING` means however many times claim-busy or the ahead-of-queue check
   fires for the same `message_sid` (Twilio retries while still busy, or
   repeated ahead-of-queue self-deferrals before this row is drained), at
   most one `deferred_inbound` row exists for it.

6. **A deferred row is marked processed only after normal processing
   succeeds.** "Succeeds" = the shared function got past claim acquisition
   and reached a definitive `try_record_message` answer - `PROCESSED` (new
   message, `advance()` ran) or `DUPLICATE` (already recorded by a prior
   attempt). `mark_processed` is called for exactly these two outcomes, never
   for `CLAIM_BUSY`.

Together: a claim-busy message is durably captured exactly once in
`deferred_inbound` (point 5), is guaranteed to eventually pass through the
*same* dedup gate as a live message (points 1-3), is recorded in
`processed_messages` exactly once no matter how many drain attempts it takes
(points 3-4), and its `deferred_inbound` row is retired only once that has
happened (point 6).

## 6. Reprocessing path

### Shared internal function

A function extracted from the current webhook body
(`src/duna_orders/web/app.py:94-151`), used by **both** the live webhook path
and the drain loop:

```
_process_validated_inbound_message(
    app,
    *,
    tenant_id: str,
    message_sid: str,
    raw_sender: str,
    customer_phone: str,
    customer_key: str,
    inbound_body: str,
    received_at: datetime,
) -> _InboundProcessingOutcome  # PROCESSED | DUPLICATE | CLAIM_BUSY | PENDING_AHEAD
```

Algorithm (the existing M9.6D body, with one new check inserted):

```
holder_id = uuid4()
if not claim_store.try_acquire(tenant_id, customer_key, holder_id):
    return CLAIM_BUSY
try:
    if deferred_inbound_store.has_pending(tenant_id, customer_key):
        return PENDING_AHEAD
    is_new = processed_message_store.try_record_message(
        message_sid, tenant_id, raw_sender, inbound_body,
    )
    if not is_new:
        return DUPLICATE
    if inbound_body.strip():
        result = advancement_service.advance(
            tenant_id=tenant_id, message_sid=message_sid,
            from_number=customer_phone, body=inbound_body,
            received_at=received_at, renew_customer_claim=...,
        )
        if result.resulting_order_id is not None:
            processed_message_store.mark_order_created(...)
    return PROCESSED
finally:
    claim_store.release(tenant_id, customer_key, holder_id)
```

This function lives in `web/app.py` (not a new module), so the existing
`CLAIM_STORE_ALLOWED_IMPORT_MODULES = {Path("src/duna_orders/web/app.py")}`
allowlist (`tests/test_architecture_boundaries.py`) needs no change -
`web/app.py` remains the sole importer of the claim-store module, and
`conversation_advancement.py` remains an unlisted (forbidden) importer, per
the existing Option B / zero-coupling design.

### Webhook handler (after this change)

```
... signature validation, MessageSid/From/Body extraction (unchanged) ...

outcome = _process_validated_inbound_message(app, tenant_id=..., message_sid=...,
    raw_sender=..., customer_phone=..., customer_key=..., inbound_body=..., received_at=...)

if outcome in (CLAIM_BUSY, PENDING_AHEAD):
    deferred_inbound_store.try_defer_message(message_sid=..., tenant_id=...,
        customer_key=..., from_number=raw_sender, raw_body=inbound_body, received_at=...)
    return Response(status_code=202)

# PROCESSED or DUPLICATE: this request held and released the claim.
return Response(
    status_code=200,
    background=BackgroundTask(_drain_deferred_for_customer, app, tenant_id, customer_key),
)
```

### Drain loop

```
def _drain_deferred_for_customer(app, tenant_id, customer_key):
    while True:
        pending = deferred_inbound_store.list_pending_for_customer(
            tenant_id, customer_key, limit=1,
        )
        if not pending:
            return
        row = pending[0]
        deferred_inbound_store.mark_attempt(row.message_sid)
        outcome = _process_validated_inbound_message(
            app, tenant_id=row.tenant_id, message_sid=row.message_sid,
            raw_sender=row.from_number,
            customer_phone=_twilio_whatsapp_sender_to_phone(row.from_number),
            customer_key=row.customer_key, inbound_body=row.raw_body,
            received_at=row.received_at,
        )
        if outcome == CLAIM_BUSY:
            return  # remaining rows wait for the next trigger; nothing dropped
        # PENDING_AHEAD cannot occur here: row is the head of its own queue
        deferred_inbound_store.mark_processed(row.message_sid)
```

`customer_phone` is recomputed from `row.from_number` via the existing
`_twilio_whatsapp_sender_to_phone` helper - the same pure function the live
webhook path already uses - so there is no second derivation to drift out of
sync (section 1 deliberately does not store a separate `customer_phone`
column for this reason).

### Why a shared function, not "re-enter the webhook"

The webhook-style entrypoint also does signature validation, form-body
parsing, and HTTP-specific extraction - none of which apply to a row already
durably stored in `deferred_inbound`. Extracting the post-validation
processing into `_process_validated_inbound_message` means the webhook and
the drain loop share *exactly* the dedup + claim + advance semantics, with
no duplicated/divergent logic, and the drain loop calls it with already-
validated, already-parsed fields read straight from `deferred_inbound`.

### If reprocessing hits claim-busy again

`_drain_deferred_for_customer` simply returns when
`_process_validated_inbound_message` reports `CLAIM_BUSY` mid-drain. The
current row (and any rows after it) remain `processed_at IS NULL` in
`deferred_inbound` - nothing is dropped, nothing is marked processed. The
next trigger (the eventual releaser's drain-on-release, or the sweep
backstop) retries from the same point, per section 3's reclaim answer.

## 7. Interaction with claim held window

Accept-and-defer changes the cost of claim-busy from "message loss risk
contingent on Twilio redelivery" (proven unsafe, section 0) to "the message
is queued and drained almost immediately, with a small added latency." This
removes the strongest argument for keeping `DEFAULT_CLAIM_LEASE_DURATION =
timedelta(seconds=60)` conservatively long - today, a too-short lease
increases claim-busy frequency, and under the pre-fix strategy each
claim-busy was a potential lost message. Under accept-and-defer, more
frequent claim-busy just means more (harmlessly) deferred-then-drained
messages.

**This makes shortening the claim lease a more attractive future tuning
lever - but it is explicitly a follow-up, not part of this design-only
slice.** `DEFAULT_CLAIM_LEASE_DURATION` is unchanged here.

## 8. Scope boundary

### In scope for M9.6D-fix-impl

* `deferred_inbound` table (new Alembic migration, section 9) and ORM row.
* `PostgresDeferredInboundStore` (narrow store outside `StorageInterface`,
  section 10) with the methods listed in section 3's "minimum viable
  version".
* Claim-busy defer-write (idempotent `try_defer_message`) replacing the
  current `return Response(status_code=503)`.
* `202 Accepted` response on claim-busy and on the new `PENDING_AHEAD`
  outcome (section 6).
* Extraction of `_process_validated_inbound_message` (section 6) shared by
  the webhook and the drain loop.
* Drain-on-release via `BackgroundTask` (section 3).
* The minimal sweep backstop: `scripts/drain_deferred_inbound.py` plus
  `list_stale_pending_customers`/reclaim-threshold logic (section 3).
* A live re-smoke per `docs/SMOKE_CLAIM_BUSY_ACCEPT_AND_DEFER.md`.

### Out of scope (for M9.6D-fix-impl and this design)

* A general queue/worker system: no `Job` table, no `SKIP LOCKED`
  multi-consumer queue, no generic retry/backoff framework. `attempt_count`
  is an observability counter only.
* A scheduler **daemon** inside the app. The sweep backstop is a standalone
  script invoked by an external scheduler (OS/hosting-provider cron), unless
  a future design proves an in-app sweep is required - this design does not
  make that case.
* Outbound WhatsApp messages, payment, the amendment flow, or any change to
  parser behavior/`PROMPT_VERSION`.
* Any UI change.
* Any change to runtime idle-boundary expiry (M9.6E remains not started; the
  M9.4E `strict=True` xfail is untouched - see section 11).
* Shortening `DEFAULT_CLAIM_LEASE_DURATION` (section 7's follow-up).

## 9. Migration note (not created in this slice)

M9.6D-fix-impl will need a new Alembic migration with `down_revision =
"5eb2de4cca12"` (the current head, added by
`alembic/versions/2026_06_11_2311-5eb2de4cca12_add_conversation_customer_claims.py`),
creating `deferred_inbound` with the columns and partial index from
section 1, following the same `op.create_table(...)` /
`sa.PrimaryKeyConstraint(..., name=op.f("pk_deferred_inbound"))` /
`downgrade(): op.drop_table(...)` style as that migration. It will also add
`DeferredInboundRow` to `postgres_models.py`, a `DEFERRED_INBOUND_TAB`
constant to `schema.py`, and update `ALEMBIC_HEAD_REVISION` in
`tests/test_smoke_preflight.py`, mirroring M9.6C's pattern for
`conversation_customer_claims`. **No migration is added in this design-only
slice.**

## 10. `StorageInterface` boundary

`deferred_inbound` follows the exact precedent of `processed_messages`,
`outbound_messages`, and `conversation_customer_claims`: a narrow
`DeferredInboundStore` Protocol plus a `PostgresDeferredInboundStore`
implementation, constructed directly from a `session_factory` (via a future
`_get_deferred_inbound_store(app)` mirroring
`_get_processed_message_store(app)`), living outside `StorageInterface` and
not built by `storage/factory.build_storage`. **No `StorageInterface` change
is required.**

No new architecture-guard allowlist entry is required either: unlike the
claim store (which `conversation_advancement.py` must *not* import, hence
M9.6D's `CLAIM_STORE_ALLOWED_IMPORT_MODULES` allowlist), there is no
"zero-coupling" concern for `deferred_inbound` -
`conversation_advancement.py` has no need to know about it at all, exactly as
it has no need to know about `processed_messages` today. `deferred_inbound`
is read/written only from `web/app.py` (the webhook handler and
`_drain_deferred_for_customer`) and from `scripts/drain_deferred_inbound.py`,
which - like other scripts under `scripts/` - constructs the app/storage
layer directly. This is the same shape `processed_messages` already has, with
no guard.

## 11. Hard-stop checklist

* **"If drain-on-release cannot be expressed without a separate
  worker/scheduler, stop and report; do not hand-wave."** Not triggered.
  Drain-on-release is a `starlette.background.BackgroundTask` attached to the
  webhook's own response - in-process, same event loop, part of the ASGI app
  already running. See section 3.
* **"If deferred reprocess cannot preserve `MessageSid` idempotency, stop and
  report."** Not triggered. Section 5 proves the full chain: not-yet-recorded
  on defer -> same dedup gate on drain -> exactly-once `processed_messages`
  row regardless of attempt count -> `deferred_inbound` row retired only after
  a terminal (`PROCESSED`/`DUPLICATE`) outcome -> unique `message_sid` on
  `deferred_inbound` prevents duplicate deferred rows.
* **"If accept-and-defer needs a `StorageInterface` change, stop and
  report."** Not triggered. Section 10: `deferred_inbound` follows the
  existing narrow-store-outside-`StorageInterface` precedent, with no new
  architecture-guard allowlist entry needed.
* **"If any implementation/migration/code change seems necessary now, stop
  and report."** Not triggered. This document, the `DECISIONS.md` entry, and
  `docs/SMOKE_CLAIM_BUSY_ACCEPT_AND_DEFER.md` are the only artifacts produced
  by this slice. No source file under `src/`, no migration under
  `alembic/versions/`, and no test file is modified.

## 12. Recommended shape for M9.6D-fix-impl

In implementation order:

1. Migration: `deferred_inbound` table + `ix_deferred_inbound_pending_by_customer`
   partial index (section 1, section 9).
2. `PostgresDeferredInboundStore` + `DeferredInboundStore` Protocol
   (section 10), with unit tests mirroring
   `tests/test_processed_message_store.py`'s style for
   `try_defer_message`/`has_pending`/`list_pending_for_customer`/
   `mark_processed`/`mark_attempt`, plus a `live_postgres` test for the
   partial index's ordering.
3. Extract `_process_validated_inbound_message` (section 6) from the current
   webhook body with no behavior change for the non-busy path; add the
   `has_pending` check and the `PENDING_AHEAD` outcome.
4. Webhook: claim-busy and `PENDING_AHEAD` -> defer-write + `202`;
   `PROCESSED`/`DUPLICATE` -> schedule `_drain_deferred_for_customer`
   `BackgroundTask`, `200` as today.
5. `_drain_deferred_for_customer` loop (section 6).
6. Webhook/advancement tests: claim-busy now defers + `202` (replacing the
   M9.6D `..._returns_503_without_processing` test's expected status and
   adding a `deferred_inbound` row assertion); ahead-of-queue defers a
   live-acquirable request; drain-on-release processes a previously-deferred
   row and marks it processed; repeated drain of an already-`processed_messages`-recorded
   row hits `DUPLICATE` and still marks `deferred_inbound` processed
   (section 5 point 4); ordering test with two deferred rows for the same
   customer drained in `received_at` order.
7. `scripts/drain_deferred_inbound.py` sweep backstop + its own
   `list_stale_pending_customers` test.
8. Live re-smoke per `docs/SMOKE_CLAIM_BUSY_ACCEPT_AND_DEFER.md`'s "future
   accept-and-defer verification" section.

Confirm before closing M9.6D-fix-impl: `pytest -q` still reports the M9.4E
`strict=True` xfail unchanged
(`tests/test_conversation_state_store.py::test_draft_created_session_remains_latest_over_later_open_session_for_customer`),
`alembic heads` shows the new revision as head, and `ruff
check`/`python -m compileall` pass.
