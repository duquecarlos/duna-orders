# M10 Stateful Parser and Conversation State Machine — Design Lock

Status: design only. No schema, no migration, no runtime changes. Baseline
`3a29cac docs(conversation): record idle expiry runtime`.

This document locks all M10 design decisions before any implementation begins.
Implementation may not start on any M10 sub-slice until this document is
present in the repo.

---

## 1. M10 goal

M10 means **true persisted conversation accumulation**: a deterministic,
inspectable accumulated draft state that is stored between turns and updated by
code — not by the LLM re-interpreting a growing transcript each time.

The defining criterion: after a session has processed N turns, it must be
possible to answer "what is the accumulated draft state after turn K?" by
reading the database, without re-running any LLM call.

### What M10 is not

**Transcript-only / no-schema** (informally "Option A" or "M10-lite") is
explicitly rejected as full M10. That approach:

- Has no persisted, inspectable accumulated state.
- Delegates all merge semantics to the LLM, making accumulation
  non-deterministic and non-auditable.
- Preserves the silent-drop failure mode: `TURN_APPENDED_INCOMPLETE` returns
  with no record of what partial state was reached.
- Cannot answer "what did we know after turn 2?" without re-running the LLM.

Transcript-only may be pursued as a separate smaller milestone (tentatively
"M10-lite" or "M9.7") if it has standalone value as a prompt improvement.
It must not be merged under the M10 label.

---

## 2. Selected approach — snapshot-diff + deterministic merge

M10 uses **Option B, snapshot-diff variant**:

1. The LLM continues to receive the full rendered transcript and continues to
   return a complete `DraftOrderRequest` snapshot. The parser's task model is
   unchanged.
2. After each parse, deterministic service/domain code computes a diff between
   the newly parsed snapshot and the previously persisted `AccumulatedDraft`
   for this session.
3. The diff result is merged into the `AccumulatedDraft` according to the
   rules in section 6.
4. The updated `AccumulatedDraft` is persisted back and becomes the session's
   source of truth for accumulation decisions.
5. Draft creation (`mark_draft_created`) is gated on the
   `AccumulatedDraft`'s completeness, not directly on the raw parser snapshot.

The LLM does **not** need to produce explicit delta operations. Code owns the
merge.

### Why snapshot-diff over explicit-delta

- The LLM's task model is unchanged for M10.1–M10.3; prompt changes are
  additive only.
- The merge/diff is pure deterministic code, independently testable.
- A later M10.x slice may extend the prompt to emit intent metadata (e.g.
  "additive" vs. "replacement" quantity language) while preserving
  `ParseResult` and parse_log shape. That extension is explicitly deferred.

---

## 3. Persistence boundary

- Accumulated draft state must be persisted under `ConversationStateStore`,
  not `StorageInterface`.
- **`StorageInterface` must remain unchanged.** This is a hard stop: any
  design path that requires adding methods to `StorageInterface` must be
  reported and redesigned before proceeding.
- New methods are added only to the `ConversationStateStore` Protocol and
  its `PostgresConversationStateStore` implementation:

  ```python
  def get_accumulated_draft(
      self,
      *,
      tenant_id: str,
      conversation_id: str,
  ) -> AccumulatedDraft | None: ...

  def save_accumulated_draft(
      self,
      *,
      tenant_id: str,
      conversation_id: str,
      draft: AccumulatedDraft,
  ) -> None: ...
  ```

- `InMemoryStorage` implements `StorageInterface`, not `ConversationStateStore`,
  and is not affected.

---

## 4. Schema — conversation_accumulated_drafts table

### Decision: separate narrow table (not a column on conversation_sessions)

A nullable JSON/TEXT column on `conversation_sessions` was considered and
rejected. Reasons:

- `conversation_sessions` rows are mutated by session lifecycle operations
  (`expire_session`, `mark_draft_created`, `record_advancement_attempt`), each
  acquiring `WITH FOR UPDATE` on the session row. Adding a frequently-updated
  accumulated draft blob to the same row increases lock contention and inflates
  unrelated version increments.
- A separate table isolates parser-turn accumulation locking from session
  lifecycle locking.
- Accumulated draft state mutates on every turn that reaches the parser;
  session metadata mutates far less frequently.
- A separate table has its own `version` and `updated_at` without entangling
  session versioning.

### Intended table: `conversation_accumulated_drafts`

```
conversation_id   STRING(80)   PRIMARY KEY
                  FK → conversation_sessions.conversation_id ON DELETE CASCADE
tenant_id         STRING(120)  NOT NULL
accumulated_json  TEXT         NOT NULL
turn_count        INTEGER      NOT NULL   -- turns applied to this draft
version           INTEGER      NOT NULL   -- optimistic lock
updated_at        DATETIME(tz) NOT NULL
```

**One row per open session.** Created on first successful parse for a session.
Updated on each subsequent parse turn. Deleted by cascade when the session row
is deleted (idle expiry creates a new `conversation_id`, so the old draft row
is never reused).

The table constant `CONVERSATION_ACCUMULATED_DRAFTS_TAB` must be added to
`storage/schema.py` in M10.1B.

The `accumulated_json` column stores the JSON serialization of `AccumulatedDraft`
(see section 5). Its format is governed by `AccumulatedDraft.schema_version`.

---

## 5. AccumulatedDraft domain type

`AccumulatedDraft` is a **new domain type**, distinct from `DraftOrderRequest`.
It represents in-progress session state, not a final order creation payload.

### Why not reuse DraftOrderRequest

`DraftOrderRequest` is a one-shot final snapshot from the parser. It cannot
distinguish "the LLM returned null for payment_method because it was not
mentioned" from "the LLM returned null because nothing is known." It has no
conflict or diagnostic metadata, no schema version marker, and no turn count.
Reusing it as accumulated state would silently erase prior values when a new
parse omits a field.

### Required fields

```python
@dataclass(frozen=True)
class AccumulatedDraftItem:
    product_id: str | None   # None when product not recognized in catalog
    quantity: Decimal
    modifications: str | None

@dataclass(frozen=True)
class AccumulatedDraft:
    schema_version: str          # e.g. "1" — JSON forward-compat marker
    tenant_id: str
    conversation_id: str
    turn_count: int              # number of parser turns applied

    # Order fields — each is Optional; None means "not yet mentioned"
    items: list[AccumulatedDraftItem]
    customer_name: str | None
    customer_phone: str | None
    fulfillment_type: str | None
    delivery_zone: str | None
    packaging_fee: Decimal
    customer_notes: str | None
    payment_method: str | None

    # Completeness / diagnostic metadata
    is_complete: bool            # computed by merge logic; stored in accumulated_json, not as a separate DB column
    conflicts: list[str]         # human-readable conflict descriptions
    warnings: list[str]          # forwarded from ParseResult.warnings
```

The actual Python shape (dataclass vs Pydantic) is left to M10.1B
implementation, but must match the above logical structure. `schema_version`
must be stored in the serialized JSON so future migrations of the JSON format
can be detected and handled.

### None vs. absent distinction

- `None` for a scalar field means "not yet mentioned in any turn."
- A non-None value means "at least one turn produced this value."
- There is no "explicitly absent" concept in M10.0 scope.
- Empty string is treated as equivalent to `None` for all scalar fields
  in the merge rules below.

---

## 6. Merge policy — M10 initial rules

### Merge function signature

```python
def merge_parse_result_into_draft(
    prior: AccumulatedDraft | None,
    parsed: DraftOrderRequest,
    turn_count: int,
    warnings: list[str],
) -> AccumulatedDraft:
    ...
```

This function must be **pure**: no I/O, no database access, no LLM calls.
It takes a prior state (or `None` on first turn) and a freshly parsed
`DraftOrderRequest` and returns the next `AccumulatedDraft`.

### Scalar field merge rules

For each scalar field (`customer_name`, `customer_phone`, `fulfillment_type`,
`delivery_zone`, `packaging_fee`, `customer_notes`, `payment_method`):

- If the parsed snapshot has a non-null, non-empty value, **update the
  accumulated field** to that value.
- If the parsed snapshot has null or empty, **keep the previously accumulated
  value** unchanged.
- This rule ensures that omitting a field in a later turn does not silently
  erase a value captured in an earlier turn.

Exception: `packaging_fee` defaults to `Decimal("0")` in `DraftOrderRequest`.
The merge must treat `Decimal("0")` from a fresh parse as "not mentioned"
(keep prior) unless the session has no prior `packaging_fee` accumulated
(first turn), in which case `0` is accepted as the initial value.

### Item merge rules — initial M10

Item merge is the most complex and most likely to require prompt extension in
a later slice. Initial M10 rules are deliberately conservative:

**Item identity**: `(product_id, modifications)` together form the item key.
Same `product_id` with same `modifications` → same logical line item.
Same `product_id` with different `modifications` → different line items.

**Additive accumulation (default)**: On each new parse, the merged item list
is produced by:
1. Taking the **parsed snapshot's item list** as the proposed state.
2. Comparing against the prior accumulated item list.
3. For each item in the prior accumulated list that has a matching
   `(product_id, modifications)` in the new snapshot: update the quantity
   to the new snapshot's value.
4. Items that appear in the prior list but are absent from the new snapshot:
   **record a conflict**, keep the item in the accumulated list, and leave
   `is_complete = False` until resolved.
5. Items that appear in the new snapshot but not the prior list: add them.

**Rationale for conservative rule 4**: The LLM returning the full transcript
should in principle return all items on every turn. If an item disappears from
the snapshot, it is more likely a parser omission than a genuine customer
retraction. Silently dropping it risks data loss. Recording a conflict and
keeping the session open is safer than either dropping or silently accepting.

**Quantity semantics**: For M10 initial policy, when the snapshot quantity for
an existing item differs from the accumulated quantity, the new snapshot value
wins (last-parse-wins for quantity). Explicit quantity correction language
("cambia a 1 pollo" / "solo 1") is expected to dominate the full transcript
and will be reflected consistently in the parsed snapshot. A later M10 slice
may add prompt-level intent metadata to distinguish additive from replacement
quantity language.

### Completeness rule

`is_complete` on `AccumulatedDraft` is computed (not stored separately):

```
is_complete = (
    len(items) > 0
    and all(item.product_id for item in items)
    and all(item.quantity > 0 for item in items)
    and len(conflicts) == 0
)
```

Customer name, fulfillment type, and payment method are **not** completeness
gates in M10. They remain non-gating unless explicitly changed by a later
milestone that alters the operator workflow. This preserves the current
behavior of `_is_complete()`.

The session must not transition to `draft_created` if `conflicts` is non-empty,
even if `items` passes validation. The session stays `open` and the next
inbound message will re-parse and potentially resolve the conflict.

### Conflict semantics

A conflict is a string description recorded in `AccumulatedDraft.conflicts`.
Conflicts must be deterministically generated (same inputs → same conflict
strings) so tests can assert on them. Examples:

- `"Item prd_pollo missing from latest parse snapshot; kept from prior turn"`

When `conflicts` is non-empty, `is_complete` is `False`. When conflicts are
resolved (the item reappears in the next parse snapshot), the conflict entry
is cleared from the list.

### Idempotency

If the same `DraftOrderRequest` is merged twice (duplicate inbound message
replayed), the resulting `AccumulatedDraft` must be identical to the result
of the first merge. The `turn_count` is supplied by the caller (tied to actual
appended turns), so a duplicate turn that is not appended does not increment
`turn_count` and does not re-merge.

---

## 7. Item identity semantics

Full statement of the item identity decision:

- **Primary key**: `(product_id, modifications)`.
- `product_id` alone is insufficient: the same product with different
  modifications (e.g. "sin sal" vs. no modification) is a distinct line item.
- Quantity comparison operates on matching `(product_id, modifications)` pairs.
- Two items with identical `(product_id, modifications)` in the same snapshot
  are a parser error; the merge function should combine their quantities and
  record a warning.
- Items without `product_id` (product not recognized in catalog) are included
  in the accumulated list with `product_id = None` and will cause
  `is_complete = False` via the standard completeness check.

**Deferred to a later M10 slice**: detecting additive vs. replacement intent
from quantity language. Until prompt-level intent metadata is added, all
quantity updates use last-parse-wins.

---

## 8. Completeness gate

The existing `_is_complete()` function in `conversation_advancement.py` checks:

```python
len(items) > 0
and all(item.product_id for item in request.items)
and all(item.quantity > 0 for item in request.items)
```

In M10.3, `_advance_open_session()` will stop using `_is_complete()` against
the raw parser snapshot and will instead check `accumulated_draft.is_complete`
after the merge. The logical completeness criteria are unchanged for M10:
same three conditions, plus `conflicts == []`.

`_is_complete()` may be retained as a private utility used inside the merge
function itself, or deleted in M10.3 once its logic is absorbed. That
decision is left to M10.3.

Customer name, fulfillment type, and payment method do not gate draft creation
in M10 and must not be added as gates without an explicit milestone decision.

---

## 9. Idle expiry interaction

Idle expiry is unchanged from M9.6E. The accumulated draft interaction is
correct by construction:

- Idle expiry calls `expire_session()` and resets routing to `None`, causing
  `get_or_create_open_session()` to produce a new session with a new
  `conversation_id`.
- The new session has no row in `conversation_accumulated_drafts` (the old row
  is not reused; FK cascade handles cleanup if the old session is ever deleted).
- The first parse turn on the new session calls `get_accumulated_draft()`,
  gets `None`, and the merge function initializes a fresh `AccumulatedDraft`.

No new lifecycle state is introduced. `open`, `draft_created`, `expired`,
`failed` are the complete set.

No explicit clearing of accumulated draft state is needed on expiry: the new
`conversation_id` provides the isolation naturally.

---

## 10. Parser, ParseResult, and parse_log constraints

These constraints are hard stops in every M10 sub-slice:

- `ParserInterface.parse(raw_message: str, products: list[Product]) -> ParseResult`
  must remain unchanged through M10.1B, M10.2, and M10.3.
- `ParseResult` shape must remain unchanged initially. Any extension (e.g.,
  adding intent metadata) requires an explicit approved design change and is
  deferred beyond M10.3.
- `parse_log.parsed_json` continues to store `result.request.model_dump_json()`
  — the parser-produced `DraftOrderRequest` snapshot — not the merged
  `AccumulatedDraft`.
- Exactly one `parse_log` row must be written per `ParsingService.parse()`
  call. This invariant must be preserved through all wiring changes in M10.3.
- `PROMPT_VERSION` must not be bumped in M10.1A (this doc), M10.1B (schema),
  or M10.2 unless the parser prompt behavior actually changes. If M10.2 adds
  prompt-level intent metadata, `PROMPT_VERSION` is bumped in M10.2 and
  documented in `DECISIONS.md`.

---

## 11. Milestone split

### M10.1A — Design lock (this document)

Status: in progress. Docs only. No code, no migration, no commit yet.

### M10.1B — Schema + store foundation (no behavior change)

Scope:

- Alembic migration: create `conversation_accumulated_drafts` table with
  columns, PK, FK → `conversation_sessions.conversation_id` ON DELETE CASCADE,
  and an index on `(tenant_id, conversation_id)`.
- Add `CONVERSATION_ACCUMULATED_DRAFTS_TAB` to `storage/schema.py`.
- Add `ConversationAccumulatedDraftsRow` to `storage/postgres_models.py`.
- Add `AccumulatedDraft` and `AccumulatedDraftItem` domain types to
  `domain/models.py`.
- Add `get_accumulated_draft` and `save_accumulated_draft` to
  `ConversationStateStore` Protocol.
- Implement both methods on `PostgresConversationStateStore` with `WITH FOR
  UPDATE` on save (optimistic version check).
- Integration tests for both store methods against the test database.

Explicitly excluded from M10.1B:

- No parser prompt change.
- No `PROMPT_VERSION` bump.
- No `merge_parse_result_into_draft` function.
- No wiring into `ConversationAdvancementService`.
- No change to `StorageInterface`.
- No change to `_is_complete()`.
- No change to advancement outcomes.

Alembic head after M10.1B: new revision with `down_revision = "d60b084798e0"`.

### M10.2 — Pure merge logic (no advancement wiring)

Scope:

- Implement `merge_parse_result_into_draft(prior, parsed, turn_count, warnings)
  -> AccumulatedDraft` as a pure function in a new module (tentatively
  `services/conversation_merge.py` or inside `services/conversation_advancement.py`
  — decide in M10.2).
- Unit tests covering: first turn (no prior), additive new item, quantity
  update on existing item, conflict on disappearing item, conflict resolution
  on reappearance, idempotent replay of same snapshot, scalar field preservation
  (null does not erase prior).
- `PROMPT_VERSION` bumped in this slice **only if** the prompt changes. If
  M10.2 adds no prompt change, `PROMPT_VERSION` stays at `"2026-05-23.1"`.

Explicitly excluded from M10.2:

- No wiring into `_advance_open_session()`.
- No database writes.
- No change to advancement outcomes or session transitions.

### M10.3 — Advancement wiring

Scope:

- In `_advance_open_session()`, after `parse_result = self._parsing_service.parse(...)`:
  1. `prior = self._conversation_state_store.get_accumulated_draft(...)`
  2. `draft = merge_parse_result_into_draft(prior, parse_result.request, turn_count, parse_result.warnings)`
  3. `self._conversation_state_store.save_accumulated_draft(..., draft=draft)`
  4. Gate `create_draft` on `draft.is_complete` instead of `_is_complete(parse_result.request, products)`.
- `turn_count` must be derived from the already-loaded ordered turn list in
  `_advance_open_session()`: use `len(turns)` where `turns` is the result of
  `list_turns(...)` already called to build the transcript. Do not use
  `prior.turn_count + 1` (fragile if prior is absent or stale) and do not
  issue a separate count query.
- If `ParsingService.parse()` raises `ParserError`, the merge and
  `save_accumulated_draft` must not run. The accumulated draft for the session
  remains unchanged. The existing `TURN_APPENDED_INCOMPLETE` return behavior
  is preserved. A failed parse must never partially mutate accumulated state.
- `parse_log` write must remain unchanged (before or after merge — parse_log
  is written by `ParsingService`, not by the advancement service).
- Preserve claim serialization: accumulated draft read/write runs inside the
  per-customer claim.
- Preserve idle expiry: new session → `get_accumulated_draft` returns `None`
  → merge initializes fresh draft.
- Tests for multi-turn accumulation (2–3 message conversation → draft created),
  conflict blocking draft creation, idle expiry reset (new session starts empty).

Explicitly excluded from M10.3:

- No outbound replies.
- No UI.
- No post-confirmation amendments.
- No new lifecycle states.

---

## 12. Hard stops — apply to every M10 sub-slice

Any of the following requires stopping, reporting, and redesigning before
proceeding:

1. `StorageInterface` change of any kind.
2. Outbound replies, bot clarification messages, or any Twilio send.
3. UI change of any kind.
4. Post-confirmation amendment logic.
5. Any new lifecycle state beyond `open`, `draft_created`, `expired`, `failed`.
6. `ParserInterface.parse()` signature change (before explicitly approved in
   a design amendment to this document).
7. Loss of one-parse-log-per-parse-call traceability.
8. `PROMPT_VERSION` bump in a slice that makes no prompt change.
9. Merger of transcript-only behavior under the M10 label.

---

## 13. Open questions (not blockers for M10.1B)

The following questions are recorded as open but do not block M10.1B
implementation:

**Q1 — Optimistic locking on save_accumulated_draft.** Should `save_accumulated_draft`
enforce that the caller's `version` matches the current DB row before writing?
This would prevent a stale merge from overwriting a newer accumulated state.
Since all writes run inside the per-customer claim, a version mismatch would
indicate a bug rather than a race. Recommendation: include optimistic lock
check in M10.1B as a safety net; raise `ValueError` on mismatch.

**Q2 — Accumulated draft on duplicate inbound.** `append_turn_if_new` returns
`appended=False` for duplicate messages. `_advance_open_session` is only called
when `turn_appended=True`. Therefore duplicate messages never reach the merge
function and `save_accumulated_draft` is never called for duplicates. This is
the correct behavior — no question, recorded for clarity.

**Q3 — When does the accumulated draft row get created vs. updated?** On the
first parser call for a session, `get_accumulated_draft` returns `None`. After
merge, `save_accumulated_draft` must INSERT (not UPDATE). On subsequent turns,
it must UPDATE. The implementation should use upsert semantics (INSERT … ON
CONFLICT DO UPDATE) or explicit read-then-write guarded by the FK row existence
check. The M10.1B implementation must specify which approach.

**Q4 — What happens to accumulated draft on mark_draft_created?** Once the
session transitions to `draft_created`, accumulated draft state is no longer
consulted (draft_created sessions return `ALREADY_HAS_DRAFT` without reaching
the parser). The accumulated draft row can remain in the database — it is
harmless and potentially useful for debugging. No cleanup action is needed in
M10.3, but a future housekeeping slice may delete it.

**Q5 — Accumulated draft and the _SpyConversationStateStore in tests.** The
`_SpyConversationStateStore` in advancement tests uses `__getattr__` delegation,
which will automatically delegate `get_accumulated_draft` and
`save_accumulated_draft` to the real store. No update to the spy should be
needed for M10.3 tests against an integrated store.
