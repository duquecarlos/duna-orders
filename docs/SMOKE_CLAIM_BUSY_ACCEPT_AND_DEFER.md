# Manual Smoke: Claim-Busy Accept-and-Defer

Purpose: manually force the per-customer conversation claim busy against a
real Twilio WhatsApp sandbox delivery, and record how the webhook responds -
first as a baseline reproduction of the failed M9.6D claim-busy-via-`503`
strategy, then (once M9.6D-fix-impl lands) as the verification procedure for
the accept-and-defer replacement.

Route under test: `POST /webhooks/twilio/whatsapp` (no trailing slash).

Do not run this smoke against production or the keeper runtime database. Use
a throwaway Neon branch.

## Status

* **Baseline (M9.6D claim-busy-via-`503`)**: already run, against baseline
  `ed31030`. **Result: FAILED** - the deferred message
  (`MessageSid SMea149d267f55a8183b3452883b140abb`) was permanently lost; no
  redelivery reached Uvicorn within ~28 minutes. Full evidence is recorded in
  the `DECISIONS.md` entry "M9.6D-fix - Accept-and-defer replaces
  claim-busy-via-503 (design only)". Phase 3a below reproduces the procedure
  that produced that result, for reference.
* **Accept-and-defer verification (M9.6D-fix-impl)**: **PASSED** — Option A
  (manual claim row) smoke run on baseline `66c2ab6`
  (`feat(web): drain deferred inbound after claim release`), Alembic head
  `d60b084798e0`, throwaway Neon branch, `DUNA_OUTBOUND_ENABLED=false` in the
  running Uvicorn process. `MessageSid SMc480bf527d5f5c81e3a43014e70c4210`
  deferred with a durable `deferred_inbound` row (`processed_at NULL`,
  `processed_messages` absent at defer time), then processed to completion by
  manual `drain_pending_deferred_inbound(...)` callable after manual claim
  deletion. Full evidence in Smoke Verdict Table and "Live Smoke Evidence —
  Option A" section below. Option A proves manual claim-busy defer + manual
  drain; automatic drain-on-release was not exercised (claim was deleted
  directly, not released through the `finally` block).
* **Automatic drain-on-release (M9.6D-fix-impl, Option B)**: **PASSED** —
  Option B (B1 two-message + B2 three-message reentrancy guard) smoke run on
  baseline `e5f5500` (`test(web): expose deferred drain suppression`), Alembic
  head `d60b084798e0`, Uvicorn 0.48.0 with 2 workers,
  `DUNA_OUTBOUND_ENABLED=false`, signed local POSTs via Cloudflared tunnel
  `https://advances-tin-characterized-jpeg.trycloudflare.com`. Automatic
  drain-on-release through the real `finally` path is live-proven. Multi-pending
  replay drains in order and exactly once. Guard-suppression INFO logs confirmed
  (1 for B1, 2 for B2). Full evidence in "Live Smoke Evidence — Option B"
  section below. **M9.6E idle-boundary expiry is unblocked.**

## Background

* `docs/M9_6D_ACCEPT_AND_DEFER_CLAIM_BUSY_DESIGN.md` - the design this smoke
  verifies.
* `DECISIONS.md`, entry "M9.6D-fix - Accept-and-defer replaces
  claim-busy-via-503 (design only)" - the live-smoke evidence that motivated
  the replacement.
* `docs/SMOKE_CHECKLIST.md` - the general inbound-webhook smoke checklist
  (happy path, duplicate `MessageSid`, signature rejection). This document
  covers only the claim-busy path and assumes familiarity with that
  checklist's setup conventions.

## Computing the correct `customer_key`

The per-customer claim key is **not** the raw Twilio `From` header. It is
derived in two steps, both of which already exist in the codebase:

```python
from duna_orders.web.inbound import _twilio_whatsapp_sender_to_phone
from duna_orders.storage.conversation_customer_claims import (
    normalize_customer_claim_key,
)

raw_sender = "whatsapp:+573223454241"          # Twilio "From" form field, verbatim
customer_phone = _twilio_whatsapp_sender_to_phone(raw_sender)
# customer_phone == "+573223454241"  (strips the "whatsapp:" prefix)

customer_key = normalize_customer_claim_key(tenant_id, customer_phone)
# customer_key == "+573223454241"  (normalize_customer_phone strips spaces/hyphens;
#                                    tenant_id is validated but not embedded in the result)
```

For the joined Twilio sandbox number used in this smoke, `From =
"whatsapp:+573223454241"`, so **`customer_key = "+573223454241"`**. Before
sending the inbound message, confirm the `From` number for *your* sandbox
session and recompute `customer_key` accordingly - it changes if the sandbox
sender phone number changes.

## Phase 0 - Pre-smoke setup

Follow `docs/SMOKE_CHECKLIST.md` Phase 0:

- [ ] Create a throwaway Neon branch for this smoke run.
- [ ] Set `DATABASE_URL` to the throwaway branch.
- [ ] Confirm `DUNA_STORAGE_BACKEND=postgres`.
- [ ] Confirm `TWILIO_AUTH_TOKEN` is the real Twilio Account Auth Token.
- [ ] Confirm `WEBHOOK_TENANT_ID=el-fogon-colombiano` (seeded demo catalog).
- [ ] Run `python scripts/smoke_preflight.py` against the throwaway branch and
  confirm no setup-blocking `FAIL` lines.
- [ ] Evidence captured:

## Phase 1 - Bring up infrastructure

Follow `docs/SMOKE_CHECKLIST.md` Phase 1:

```powershell
$env:DUNA_STORAGE_BACKEND="postgres"
uvicorn duna_orders.web.app:app --host 127.0.0.1 --port 8000 --reload
```

```powershell
cloudflared tunnel --url http://127.0.0.1:8000
```

```powershell
$env:TWILIO_WEBHOOK_PUBLIC_URL="https://<host>.trycloudflare.com/webhooks/twilio/whatsapp"
```

- [ ] Restart FastAPI after setting `TWILIO_WEBHOOK_PUBLIC_URL`.
- [ ] Configure the Twilio WhatsApp sandbox inbound webhook (`POST`,
  `application/x-www-form-urlencoded`) to the same URL.
- [ ] Confirm the path is exactly `/webhooks/twilio/whatsapp`.
- [ ] Evidence captured:

## Phase 2 - Force claim-busy via manual claim insertion

Before sending the test message, manually insert a
`conversation_customer_claims` row for the sandbox sender's `customer_key`
(computed above), with a `lease_expires_at` far enough in the future to hold
the claim busy for the duration of this smoke (e.g. 30 minutes):

```sql
INSERT INTO conversation_customer_claims
    (tenant_id, customer_key, holder_id, acquired_at, lease_expires_at, updated_at)
VALUES
    ('el-fogon-colombiano', '+573223454241', 'manual-smoke-claim-busy',
     now(), now() + interval '30 minutes', now());
```

- [ ] Confirm the row was inserted:

```sql
SELECT tenant_id, customer_key, holder_id, lease_expires_at
FROM conversation_customer_claims
WHERE tenant_id = 'el-fogon-colombiano' AND customer_key = '+573223454241';
```

- [ ] Evidence captured (row present, `lease_expires_at` in the future):

## Phase 3a - Baseline reproduction: claim-busy-via-`503` (already run; expected to fail)

This phase reproduces the procedure that produced the FAILED result recorded
in `DECISIONS.md`. Run this only against a baseline **before**
M9.6D-fix-impl lands (i.e. while `try_acquire` failure still returns `503`).

- [ ] Send one real WhatsApp message from the joined sandbox number (any
  text).
- [ ] Capture the `MessageSid` from the Twilio Request Inspector:
- [ ] Confirm Uvicorn logs `"POST /webhooks/twilio/whatsapp HTTP/1.1" 503
  Service Unavailable`.
- [ ] Confirm the Twilio Request Inspector shows HTTP `503` with warning
  `11200` for this delivery.
- [ ] Verify `processed_messages` has **zero** rows for this `MessageSid`:

```sql
SELECT count(*) AS sid_rows FROM processed_messages WHERE message_sid = '<MessageSid>';
-- expected: 0
```

- [ ] **Expected current result: `503`, `sid_rows = 0`.** This matches the
  recorded baseline (`MessageSid SMea149d267f55a8183b3452883b140abb`,
  `2026-06-12 19:01:22 UTC`).
- [ ] Do **not** wait for redelivery as a pass condition - the recorded
  baseline shows no redelivery arrives within ~28 minutes, and the message is
  permanently lost under this (pre-fix) strategy. If you choose to observe
  this for confirmation, note the elapsed wait time and whether any
  redelivery was logged; record it under "Latest Result" below, but a `FAIL`
  here is the **expected, already-documented** outcome for the pre-fix
  strategy, not a new finding.
- [ ] Evidence captured:

## Phase 3b - Future verification: accept-and-defer (`202`) - run only after M9.6D-fix-impl

Run this phase instead of (or in addition to, on a separate `MessageSid`)
Phase 3a once M9.6D-fix-impl has landed and the manual claim from Phase 2 is
still held.

- [ ] Send one real WhatsApp message from the joined sandbox number (any
  text, distinct `MessageSid` from Phase 3a if both are run in the same
  session).
- [ ] Capture the `MessageSid`:
- [ ] Confirm Uvicorn logs `"POST /webhooks/twilio/whatsapp HTTP/1.1" 202
  Accepted` (not `503`).
- [ ] Confirm the Twilio Request Inspector shows a `2xx` status for this
  delivery (no warning `11200`).
- [ ] Verify a `deferred_inbound` row exists for this `MessageSid`, with
  `processed_at IS NULL`:

```sql
SELECT message_sid, tenant_id, customer_key, received_at, deferred_at, processed_at
FROM deferred_inbound
WHERE message_sid = '<MessageSid>';
-- expected: one row, processed_at IS NULL
```

- [ ] Verify `processed_messages` still has **zero** rows for this
  `MessageSid` (it has not been processed yet - only deferred):

```sql
SELECT count(*) AS sid_rows FROM processed_messages WHERE message_sid = '<MessageSid>';
-- expected: 0
```

- [ ] **Expected result: `202`, `deferred_inbound` row present
  (`processed_at IS NULL`), `processed_messages` still `sid_rows = 0`.**
- [ ] Evidence captured:

## Phase 4 - Release the claim and trigger the drain (future)

- [ ] Release the manually-held claim from Phase 2, either by deleting it or
  by expiring its lease immediately (either is sufficient for `try_acquire`'s
  takeover condition `lease_expires_at <= now()`):

```sql
DELETE FROM conversation_customer_claims
WHERE tenant_id = 'el-fogon-colombiano'
  AND customer_key = '+573223454241'
  AND holder_id = 'manual-smoke-claim-busy';
```

- [ ] Trigger the drain via **one** of:
  * Send a second real WhatsApp message from the same sandbox number. Its
    webhook request finds the claim free, processes live, and (per the
    design's drain-on-release) schedules `_drain_deferred_for_customer` as a
    background task after responding - which drains the Phase 3b row.
  * Run the sweep backstop script directly:

    ```powershell
    python scripts/drain_deferred_inbound.py
    ```

- [ ] Evidence captured (which trigger was used):

## Phase 5 - Verify drain results (future)

- [ ] Verify `processed_messages` now has exactly one row for the Phase 3b
  `MessageSid`:

```sql
SELECT message_sid, tenant_id, resulting_order_id
FROM processed_messages
WHERE message_sid = '<MessageSid from Phase 3b>';
-- expected: one row
```

- [ ] Verify the `deferred_inbound` row for that `MessageSid` is now marked
  processed:

```sql
SELECT message_sid, processed_at
FROM deferred_inbound
WHERE message_sid = '<MessageSid from Phase 3b>';
-- expected: processed_at IS NOT NULL
```

- [ ] Verify `conversation_turns` contains a turn for the Phase 3b message,
  ordered (by `sequence_number`) after any turn from a message that was *not*
  deferred but arrived earlier - i.e. `received_at` order was preserved.
- [ ] If the deferred message, combined with prior conversation turns, formed
  a complete order, verify exactly one new `orders` row was created and
  `processed_messages.resulting_order_id` points to it.
- [ ] **Expected result**: `processed_messages` row present for the Phase 3b
  `MessageSid`, `deferred_inbound.processed_at` populated, conversation turn
  ordering preserved, and (if applicable) order creation completed.
- [ ] Evidence captured:

## Teardown

Follow `docs/SMOKE_CHECKLIST.md` Phase 5:

- [ ] Remove any leftover manual `conversation_customer_claims` rows inserted
  for this smoke (Phase 2), if not already removed in Phase 4:

```sql
DELETE FROM conversation_customer_claims WHERE holder_id = 'manual-smoke-claim-busy';
```

- [ ] Stop the cloudflared tunnel.
- [ ] Stop the local FastAPI app.
- [ ] Remove or replace the Twilio sandbox inbound webhook URL.
- [ ] Delete the throwaway Neon branch or confirm its auto-delete window.
- [ ] Restore local `.env` to point at the keeper branch.
- [ ] Evidence captured:

## Smoke Verdict Table

| Check | Result | Evidence |
| --- | --- | --- |
| Phase 3a: claim-busy returns `503`, `sid_rows = 0` (baseline, already run) | FAILED (message permanently lost - see `DECISIONS.md`) | `MessageSid SMea149d267f55a8183b3452883b140abb`, first `503` at `2026-06-12 19:01:22 UTC`, no redelivery by `2026-06-12 19:29:12 UTC` |
| Phase 3b: claim-busy defers durably, `deferred_inbound` row present, `processed_messages` absent | PASSED (Option A, 2026-06-13) | `MessageSid SMc480bf527d5f5c81e3a43014e70c4210`; `deferred_inbound` row written with `processed_at NULL`; duplicate signed POST with same sid returned `202` with exactly one row remaining; `processed_messages` count = 0 at defer time |
| Phase 4-5: drain processes the deferred row, `processed_messages` row created, ordering preserved | PASSED (Option A, 2026-06-13) | Manual `drain_pending_deferred_inbound(...)` callable; summary `processed=['SMc480...']`, `still_pending=[]`, `failed=[]`; `processed_at` populated; `processed_messages` row exists (count = 1); `attempt_count = 1`; conversation turn appended with original `received_at` preserved |
| Option B B1: automatic drain-on-release (two-message) | PASSED (2026-06-13, baseline `e5f5500`) | SIDs `SM_OPT_B1_A_003`/`SM_OPT_B1_B_003`; B returned `202`, `deferred_inbound` row with `processed_at NULL` before drain; post-drain `processed_at=05:06:02 UTC`, `attempt_count=1`, both SIDs in `processed_messages`, `still_pending=0`; guard-suppression INFO log present |
| Option B B2: multi-pending + reentrancy guard (three-message) | PASSED (2026-06-13, baseline `e5f5500`) | SIDs `SM_OPT_B2_A_001`/`SM_OPT_B2_B_001`/`SM_OPT_B2_C_001`; B+C both `202`; post-drain: B `processed_at=05:06:13 UTC`, C `processed_at=05:06:17 UTC`, all three in `processed_messages`, drain order B then C, `still_pending=0`; guard-suppression INFO lines exactly 2 |

## Notes / Observations

```text
customer_key is the normalized phone (e.g. "+573223454241"), NOT the raw
Twilio "From" value ("whatsapp:+573223454241"). Recompute it for your own
sandbox sender before Phase 2 - see "Computing the correct customer_key"
above.

DEFAULT_CLAIM_LEASE_DURATION = 60 seconds. The manual claim in Phase 2 uses a
much longer lease (30 minutes) so it stays busy for the whole smoke session
regardless of how long Phases 2-3 take.

Phase 3a's expected "FAIL" (message lost) is the documented baseline this
smoke exists to move past - it is not a new finding each time this phase is
re-run before M9.6D-fix-impl lands.
```

## Live Smoke Evidence — Option A (2026-06-13)

**Baseline**: `66c2ab6 feat(web): drain deferred inbound after claim release`
**Alembic head**: `d60b084798e0`
**Method**: Option A — manual `conversation_customer_claims` row
(`holder_id='manual-smoke'`, `lease_expires_at = now() + 30 minutes`) inserted
before the WhatsApp send; deleted before invoking the drain.

### Environment

| Setting | Value |
| --- | --- |
| `DUNA_STORAGE_BACKEND` | `postgres` |
| Throwaway Neon branch | Confirmed |
| `DUNA_OUTBOUND_ENABLED` | `false` in running Uvicorn process |
| `WEBHOOK_TENANT_ID` | `el-fogon-colombiano` |
| Secrets printed | None |
| Code edits | None |
| Commit / push | None |

### Defer-path evidence (before drain)

**Message sent**: body `smoke claim busy test`, sender `whatsapp:+573223454241`.

The durable `deferred_inbound` row is proof the defer path ran and the webhook
returned `202`. (The `202` branch is the only branch that calls
`defer_message(...)` successfully; the `503` fallback fires only when
`defer_message` itself raises, which would leave no row.)

| Field | Value |
| --- | --- |
| `message_sid` | `SMc480bf527d5f5c81e3a43014e70c4210` |
| `tenant_id` | `el-fogon-colombiano` |
| `customer_key` | `+573223454241` |
| `from_number` | `whatsapp:+573223454241` |
| `raw_body` | `smoke claim busy test` |
| `received_at` | `2026-06-13 02:26:51 UTC` |
| `deferred_at` | `2026-06-13 02:26:57 UTC` |
| `processed_at` | NULL |
| `processing_started_at` | NULL |
| `attempt_count` | 0 |

**`processed_messages` at defer time**: 0 rows for this `MessageSid`. ✓

**Parser / advance / order / session mutations at defer time**: None. No new
conversation session, no new orders, no new conversation turns for
`+573223454241`. ✓

**Duplicate defer idempotency**: A second signed local webhook `POST` with the
same `MessageSid` while the manual claim was still held returned `202` and left
exactly one `deferred_inbound` row (`attempt_count` still 0).
`processed_messages` remained empty. ✓

### Drain evidence

**Drain method**: manual callable —
`drain_pending_deferred_inbound(app, tenant_id='el-fogon-colombiano')` invoked
directly after manual claim row deletion via `DELETE FROM
conversation_customer_claims WHERE holder_id = 'manual-smoke'`.

**Drain summary**:
```
processed:     ['SMc480bf527d5f5c81e3a43014e70c4210']
still_pending: []
failed:        []
```

### Post-drain evidence

| Check | Result |
| --- | --- |
| `deferred_inbound.processed_at` | `2026-06-13 02:32:06 UTC` — populated ✓ |
| `deferred_inbound.processing_started_at` | `2026-06-13 02:31:36 UTC` ✓ |
| `deferred_inbound.attempt_count` | 1 ✓ |
| `processed_messages` row count for sid | exactly 1 ✓ |
| `processed_messages.from_number` | `whatsapp:+573223454241` ✓ |
| `processed_messages.raw_body` | `smoke claim busy test` ✓ |
| `processed_messages.resulting_order_id` | NULL (body was intentionally non-ordering) ✓ |
| Conversation turn appended | Yes — `sequence_number=4`, `received_at=2026-06-13 02:26:51 UTC` (original, not drain time) ✓ |
| New orders since smoke start | 0 ✓ |
| Remaining pending `deferred_inbound` rows for tenant | 0 ✓ |

### Post-smoke verification

| Command | Result |
| --- | --- |
| `git status --short` | Clean |
| `alembic heads` | `d60b084798e0 (head)` |
| `pytest tests/test_web_twilio_webhook.py -q` | 41 passed |
| `pytest tests/test_deferred_inbound.py tests/test_processed_messages.py tests/test_conversation_customer_claim_store.py -q` | 26 passed, 10 deselected |
| `git diff --check` | Clean |

### What was not proven

Automatic drain-on-release was not exercised. The manual claim was deleted
directly via SQL, not released through `_process_validated_inbound_message`'s
`finally` block, so the `drain_pending_deferred_inbound_for_customer(...)` call
wired into `finally` was never reached. This is an inherent limitation of
Option A. The automatic path is covered by six passing unit tests
(`test_twilio_webhook_auto_drain_on_release_*`). A live Option B smoke — two
real WhatsApp messages back-to-back, with the second arriving while the first
message's `advance()` call is still in flight — provides timing-dependent
end-to-end proof. Option B was subsequently performed (baseline `e5f5500`,
2026-06-13) and passed; see "Live Smoke Evidence — Option B" below.

---

## Option B — Automatic drain-on-release (live webhook `finally` path)

### Purpose and scope

Prove that `_process_validated_inbound_message`'s `finally` block
automatically drains pending deferred rows for a customer after the real code
path releases the claim. Option A proved the defer path and manual drain;
Option B proves the automatic path.

**Option B does not use a manual claim row.** The claim is held naturally by a
live call to `advance()`, which makes a real Anthropic API call and holds the
claim for approximately 2–5 s. Deferred messages are sent as signed local
httpx POSTs while `advance()` is blocking; the automatic drain fires in the
`finally` block when the real claim is released.

### Prerequisites — confirm before every Option B run

- [ ] All Phase 0 and Phase 1 steps complete: throwaway Neon branch,
  `DUNA_OUTBOUND_ENABLED=false` in the running Uvicorn process, uvicorn
  listening on `127.0.0.1:8000`, cloudflared tunnel active,
  `TWILIO_WEBHOOK_PUBLIC_URL` set to the tunnel URL, `TWILIO_AUTH_TOKEN`
  available in the environment.
- [ ] `TWILIO_WEBHOOK_PUBLIC_URL` is the **tunnel URL**, not localhost. The
  signature validator computes the HMAC against this URL even though the HTTP
  POST goes to localhost. If the URL is wrong, every signed local POST receives
  `403`.
- [ ] No existing pending `deferred_inbound` rows for the test customer_key:

```sql
SELECT message_sid, processed_at
FROM deferred_inbound
WHERE tenant_id = 'el-fogon-colombiano'
  AND customer_key = '+573223454241'
  AND processed_at IS NULL;
-- expected: 0 rows
```

- [ ] No existing `conversation_customer_claims` row for the test customer_key:

```sql
SELECT holder_id, lease_expires_at
FROM conversation_customer_claims
WHERE tenant_id = 'el-fogon-colombiano' AND customer_key = '+573223454241';
-- expected: 0 rows
```

**Hard stops — abort and resolve before proceeding:**

1. If any pending `deferred_inbound` row exists for this customer_key: drain
   or delete it first, or use a different customer phone number.
2. If `DUNA_OUTBOUND_ENABLED=true` in the running process: abort immediately.
   Option B triggers `advance()`, which would send a real outbound WhatsApp
   message to the customer.
3. If `TWILIO_WEBHOOK_PUBLIC_URL` is unset or points to localhost: the
   signature check rejects every signed local POST. Reconfigure and restart
   uvicorn before proceeding.
4. If a `conversation_customer_claims` row already exists for this
   customer_key: delete it or wait for its lease to expire before starting.

### Signed local POST helper

Messages B (and C in B2) are sent as signed local httpx POSTs. Message A may
also be sent this way — the Anthropic API latency comes from `advance()`, not
from Twilio delivery overhead. Open a Python REPL and paste this helper once:

```python
import os, httpx
from twilio.request_validator import RequestValidator

AUTH_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]
PUBLIC_URL = os.environ["TWILIO_WEBHOOK_PUBLIC_URL"]   # must be tunnel URL
LOCAL_URL  = "http://127.0.0.1:8000/webhooks/twilio/whatsapp"

def signed_post(message_sid: str, body: str, timeout: float = 10.0) -> int:
    params = {
        "From":          "whatsapp:+573223454241",
        "To":            "whatsapp:+14155238886",   # Twilio sandbox number
        "Body":          body,
        "MessageSid":    message_sid,
        "SmsMessageSid": message_sid,
        "NumMedia":      "0",
        "AccountSid":    os.environ.get("TWILIO_ACCOUNT_SID", "AC_smoke"),
    }
    sig = RequestValidator(AUTH_TOKEN).compute_signature(PUBLIC_URL, params)
    resp = httpx.post(
        LOCAL_URL, data=params,
        headers={"X-Twilio-Signature": sig},
        timeout=timeout,
    )
    print(f"  {message_sid}: HTTP {resp.status_code}")
    return resp.status_code
```

Replace the `"To"` value with your sandbox's actual Twilio WhatsApp number if
it differs from `+14155238886`.

---

### B1 — Two-message automatic drain

**Goal**: A (parseable body) triggers `advance()` and holds the claim. B
arrives for the same customer_key while A is blocking. B defers (202).
A's `finally` block releases the claim and automatically drains B. No manual
drain is called.

#### B1 setup — verify SIDs are clean

```python
SID_A = "SM_OPT_B1_A_001"
SID_B = "SM_OPT_B1_B_001"
```

```sql
SELECT message_sid FROM processed_messages
WHERE message_sid IN ('SM_OPT_B1_A_001', 'SM_OPT_B1_B_001');
-- expected: 0 rows

SELECT message_sid FROM deferred_inbound
WHERE message_sid IN ('SM_OPT_B1_A_001', 'SM_OPT_B1_B_001');
-- expected: 0 rows
```

If either SID already exists: choose different SID strings.

#### B1 dispatch — send A then B

Paste into the same Python REPL where `signed_post` is defined:

```python
import threading, time

def send_a():
    status = signed_post(SID_A, "Un bandeja paisa por favor", timeout=30)
    print(f"A returned: HTTP {status}")

t_a = threading.Thread(target=send_a, daemon=True)
t_a.start()

# Brief yield — enough for A to reach try_acquire and advance()
time.sleep(0.5)

# Send B while A's advance() is blocking
status_b = signed_post(SID_B, "Y una limonada", timeout=10)
print(f"B returned: HTTP {status_b}")   # must be 202

t_a.join()
print("A thread joined; automatic drain has already fired in the finally block")
```

**Timing constraint**: B must arrive while A's `advance()` is blocking. The
Anthropic API call inside `advance()` takes approximately 2–5 s, so the 0.5 s
yield before sending B provides sufficient margin under normal conditions.

**If the timing window is missed** — A returns before B is dispatched, so B
finds the claim free and is PROCESSED (not DEFERRED): the test is **invalid**.
Discard both SIDs, choose fresh ones, and repeat from B1 setup. The symptom is
`status_b != 202` or no `deferred_inbound` row for B.

#### B1 mid-run check — confirm B deferred (after B's POST returns, before A's thread joins)

```sql
SELECT message_sid, deferred_at, processed_at, attempt_count
FROM deferred_inbound
WHERE message_sid = 'SM_OPT_B1_B_001';
-- expected: 1 row, processed_at IS NULL
```

```sql
SELECT count(*) AS pm_count FROM processed_messages
WHERE message_sid = 'SM_OPT_B1_B_001';
-- expected: 0
```

#### B1 post-drain evidence — collect after `t_a.join()` returns

A's `finally` block has fired: `release` freed the claim, then
`drain_pending_deferred_inbound_for_customer(limit=5)` replayed B.

```sql
-- B must now be marked processed
SELECT message_sid, processed_at, attempt_count
FROM deferred_inbound
WHERE message_sid = 'SM_OPT_B1_B_001';
-- expected: processed_at IS NOT NULL, attempt_count = 1
```

```sql
-- B must have a processed_messages row
SELECT message_sid, received_at, resulting_order_id
FROM processed_messages
WHERE message_sid = 'SM_OPT_B1_B_001';
-- expected: 1 row
```

```sql
-- A must also have a processed_messages row
SELECT message_sid, received_at, resulting_order_id
FROM processed_messages
WHERE message_sid = 'SM_OPT_B1_A_001';
-- expected: 1 row
```

```sql
-- No pending rows remaining for this customer
SELECT count(*) AS still_pending
FROM deferred_inbound
WHERE tenant_id = 'el-fogon-colombiano'
  AND customer_key = '+573223454241'
  AND processed_at IS NULL;
-- expected: 0
```

**Uvicorn log evidence**: confirm `"Automatic drain-on-release failed"` does
**not** appear in the log. That error line is only emitted when the drain
raises an unhandled exception; its absence confirms the drain completed
without error. The drain runs synchronously in the `finally` block before A's
response is delivered to the HTTP client, so A's `"202 Accepted"` access log
line appears only after B's drain is complete.

#### B1 PASS/FAIL

| Check | Expected |
| --- | --- |
| `status_b` (B POST return code) | `202` |
| `deferred_inbound` row for B before `t_a.join()` | present, `processed_at IS NULL` |
| `deferred_inbound.processed_at` for B after `t_a.join()` | `NOT NULL` |
| `deferred_inbound.attempt_count` for B | `1` |
| `processed_messages` row for B | exactly 1 |
| `processed_messages` row for A | exactly 1 |
| `still_pending` count for customer | `0` |
| `"Automatic drain-on-release failed"` in Uvicorn log | absent |
| Manual drain called | No |

**FAIL if** any of: `status_b != 202`; `deferred_inbound.processed_at` for B
remains NULL after A's thread returns; `processed_messages` count for B is 0
or > 1; the failure log line appears; `still_pending > 0`.

---

### B2 — Three-message multi-pending and reentrancy guard

B2 requires **positive evidence** that the reentrancy guard fired — that when
the drain-triggered replay of B releases B's claim, the inner call to
`drain_pending_deferred_inbound_for_customer` is suppressed rather than
recursing. This prevents unbounded nesting (B draining C, C draining D, …).

**The guard is implemented structurally**: `_replay_deferred_records` always
passes `auto_drain_after_release=False` to every `_process_validated_inbound_message`
replay call. In the `finally` block of each replayed call the suppression path
emits an `INFO`-level log line:

```python
else:
    logger.info(
        "deferred inbound auto-drain suppressed after claim release "
        "(auto_drain_after_release=False) for tenant_id=%s customer_key=%s",
        tenant_id,
        customer_key,
    )
```

This is visible at the default `LOG_LEVEL=INFO` with no configuration change.
A unit test (`test_process_validated_inbound_message_logs_suppressed_drain`)
verifies the line is emitted.

**Hard stop**: if the expected `INFO` suppression lines do **not** appear in
the Uvicorn log after A's request completes, stop. The running process may not
have the current code. Restart uvicorn from the updated source and rerun B2.

#### B2 setup

Assign three fresh SIDs:

```python
SID_A = "SM_OPT_B2_A_001"
SID_B = "SM_OPT_B2_B_001"
SID_C = "SM_OPT_B2_C_001"
```

```sql
SELECT message_sid FROM processed_messages
WHERE message_sid IN ('SM_OPT_B2_A_001', 'SM_OPT_B2_B_001', 'SM_OPT_B2_C_001');
-- expected: 0 rows

SELECT message_sid FROM deferred_inbound
WHERE message_sid IN ('SM_OPT_B2_A_001', 'SM_OPT_B2_B_001', 'SM_OPT_B2_C_001');
-- expected: 0 rows
```

#### B2 dispatch — send A, then B and C while A holds the claim

```python
import threading, time

def send_a():
    status = signed_post(SID_A, "Un bandeja paisa por favor", timeout=30)
    print(f"A returned: HTTP {status}")

t_a = threading.Thread(target=send_a, daemon=True)
t_a.start()

time.sleep(0.5)   # A reaches advance()

status_b = signed_post(SID_B, "Y una limonada", timeout=10)
print(f"B returned: HTTP {status_b}")   # must be 202

status_c = signed_post(SID_C, "Con arepa", timeout=10)
print(f"C returned: HTTP {status_c}")   # must be 202

t_a.join()
print("A thread joined; B and C should both be drained")
```

B and C are sent in rapid succession while A's `advance()` is blocking. Both
should defer. After A's `finally` block fires, `_replay_deferred_records`
processes B then C in `received_at` order, passing `auto_drain_after_release=False`
to each replay — causing the instrumentation log line to appear exactly twice.

#### B2 post-drain evidence queries

```sql
-- B and C must both be marked processed
SELECT message_sid, processed_at, attempt_count
FROM deferred_inbound
WHERE message_sid IN ('SM_OPT_B2_B_001', 'SM_OPT_B2_C_001')
ORDER BY processed_at;
-- expected: 2 rows, both processed_at IS NOT NULL, attempt_count = 1 each
```

```sql
-- All three have processed_messages rows
SELECT message_sid FROM processed_messages
WHERE message_sid IN ('SM_OPT_B2_A_001', 'SM_OPT_B2_B_001', 'SM_OPT_B2_C_001');
-- expected: 3 rows
```

```sql
-- No pending rows remain
SELECT count(*) AS still_pending
FROM deferred_inbound
WHERE tenant_id = 'el-fogon-colombiano'
  AND customer_key = '+573223454241'
  AND processed_at IS NULL;
-- expected: 0
```

**Guard-suppression positive evidence**: grep the Uvicorn log for the
suppression line. It must appear **exactly twice** after A's request completes
— once for B's replay, once for C's replay:

```
INFO     duna_orders.web.app:app.py - deferred inbound auto-drain suppressed after claim release (auto_drain_after_release=False) for tenant_id=el-fogon-colombiano customer_key=+573223454241
INFO     duna_orders.web.app:app.py - deferred inbound auto-drain suppressed after claim release (auto_drain_after_release=False) for tenant_id=el-fogon-colombiano customer_key=+573223454241
```

(Log format may vary slightly by uvicorn configuration; the required phrase is
`deferred inbound auto-drain suppressed after claim release`.)

Exactly two lines: one per deferred message replayed. Zero lines means the
running process does not have the current code — restart uvicorn and retry.
More than two lines indicates unexpected recursion.

#### B2 PASS/FAIL

| Check | Expected |
| --- | --- |
| `status_b` and `status_c` | `202` each |
| `deferred_inbound` rows for B and C before `t_a.join()` | present, `processed_at IS NULL` |
| `deferred_inbound.processed_at` for B and C after `t_a.join()` | `NOT NULL` for both |
| `deferred_inbound.attempt_count` for B and C | `1` each |
| `processed_messages` rows for A, B, C | exactly 1 each |
| `still_pending` count for customer | `0` |
| `"Automatic drain-on-release failed"` in Uvicorn log | absent |
| Guard-suppression `INFO` lines in Uvicorn log | exactly 2 |
| Manual drain called | No |

**FAIL if** any of: either B or C has `processed_at IS NULL` after A's thread
returns; `processed_messages` count for B or C is 0 or > 1; the drain-failure
error line appears; `still_pending > 0`; guard-suppression `INFO` lines are 0
(process not running current code) or > 2 (recursion detected).

---

### Option B teardown

Same as the Teardown section. No manual claim row was inserted in Option B,
so the `DELETE FROM conversation_customer_claims` step is optional. Verify the
claim was released automatically:

```sql
SELECT * FROM conversation_customer_claims
WHERE tenant_id = 'el-fogon-colombiano' AND customer_key = '+573223454241';
-- expected: 0 rows
```

---

## Live Smoke Evidence — Option B (2026-06-13)

**Baseline**: `e5f5500 test(web): expose deferred drain suppression`
**Alembic head**: `d60b084798e0`
**Method**: Option B — signed local httpx POSTs via Cloudflared tunnel, no
manual claim row, no manual drain. Claim held naturally by a live `advance()`
call (real Anthropic API). Two sub-scenarios:

* **B1**: two messages (A primary, B deferred then auto-drained).
* **B2**: three messages (A primary, B and C deferred then auto-drained in
  order, reentrancy guard confirmed).

### Environment

| Setting | Value |
| --- | --- |
| Uvicorn | 0.48.0, 2 workers (PIDs 1164 + 22120) |
| `DUNA_OUTBOUND_ENABLED` | `false` (shell override of `.env` `true`) |
| `DUNA_STORAGE_BACKEND` | `postgres` |
| Cloudflared tunnel | `https://advances-tin-characterized-jpeg.trycloudflare.com` |
| Transport | HMAC-signed `httpx.post` to `http://127.0.0.1:8000/webhooks/twilio/whatsapp` |
| Log capture | `uvicorn_smoke_b.log` via `log_config.json` root logger at INFO |
| Tenant | `el-fogon-colombiano` |
| `customer_key` | `+573223454241` |
| Secrets printed | None |
| Code edits | None |
| Commit / push | None |
| Manual drain invoked | No |

### B1 — Two-message automatic drain

SIDs: A = `SM_OPT_B1_A_003`, B = `SM_OPT_B1_B_003`.

**Dispatch**: A dispatched in a background thread. DB polling confirmed A's
claim row appeared in `conversation_customer_claims` before B was sent. B sent
immediately after claim confirmed.

| Check | Result |
| --- | --- |
| B POST HTTP status | `202` ✓ |
| B `deferred_inbound` row present before A returns | `deferred_at=05:05:56 UTC`, `processed_at NULL` ✓ |
| B absent from `processed_messages` before A returns | count=0 ✓ |
| B `deferred_inbound.processed_at` after A joins | `05:06:02 UTC` — populated ✓ |
| B `deferred_inbound.attempt_count` | `1` ✓ |
| B `processed_messages` row | 1 row, `received_at=05:06:00 UTC` ✓ |
| A `processed_messages` row | 1 row, `received_at=05:05:53 UTC` ✓ |
| `still_pending` for customer | `0` ✓ |
| `"Automatic drain-on-release failed"` in log | absent ✓ |
| Guard-suppression INFO log | 1 line present ✓ |

**B1 OVERALL: PASS 10/10**

### B2 — Three-message multi-pending + reentrancy guard

SIDs: A = `SM_OPT_B2_A_001`, B = `SM_OPT_B2_B_001`, C = `SM_OPT_B2_C_001`.

**Dispatch**: A dispatched in a background thread. DB polling confirmed A's
claim before B was sent. B sent immediately, then C sent immediately after.

| Check | Result |
| --- | --- |
| B POST HTTP status | `202` ✓ |
| C POST HTTP status | `202` ✓ |
| B+C `deferred_inbound` rows before A returns | present, both `processed_at NULL` ✓ |
| B+C absent from `processed_messages` before A returns | count=0 ✓ |
| B `deferred_inbound.processed_at` | `05:06:13 UTC` ✓ |
| C `deferred_inbound.processed_at` | `05:06:17 UTC` ✓ |
| B `attempt_count` | `1` ✓ |
| C `attempt_count` | `1` ✓ |
| `processed_messages` rows for A, B, C | exactly 1 each ✓ |
| Drain order (by `processed_at`) | B then C ✓ |
| `still_pending` for customer | `0` ✓ |
| `"Automatic drain-on-release failed"` in log | absent ✓ |
| Guard-suppression INFO lines | exactly 2 (one per replayed deferred message) ✓ |

**B2 OVERALL: PASS 12/12 DB checks + 2/2 guard log lines**

### Guard-suppression log evidence

Uvicorn log search phrase: `deferred inbound auto-drain suppressed after claim release`

Relevant sequence from `uvicorn_smoke_b.log`:

```
INFO:     duna_orders.web.app: Conversation claim busy for tenant_id=el-fogon-colombiano customer_key=+573223454241 message_sid=SM_OPT_B1_B_003; deferring for later processing
INFO:     127.0.0.1:58629 - "POST /webhooks/twilio/whatsapp HTTP/1.1" 202 Accepted
INFO:     duna_orders.web.app: deferred inbound auto-drain suppressed after claim release (auto_drain_after_release=False) for tenant_id=el-fogon-colombiano customer_key=+573223454241
INFO:     127.0.0.1:58627 - "POST /webhooks/twilio/whatsapp HTTP/1.1" 200 OK
INFO:     duna_orders.web.app: Conversation claim busy for tenant_id=el-fogon-colombiano customer_key=+573223454241 message_sid=SM_OPT_B2_B_001; deferring for later processing
INFO:     127.0.0.1:58637 - "POST /webhooks/twilio/whatsapp HTTP/1.1" 202 Accepted
INFO:     duna_orders.web.app: Conversation claim busy for tenant_id=el-fogon-colombiano customer_key=+573223454241 message_sid=SM_OPT_B2_C_001; deferring for later processing
INFO:     127.0.0.1:58639 - "POST /webhooks/twilio/whatsapp HTTP/1.1" 202 Accepted
INFO:     duna_orders.web.app: deferred inbound auto-drain suppressed after claim release (auto_drain_after_release=False) for tenant_id=el-fogon-colombiano customer_key=+573223454241
INFO:     duna_orders.web.app: deferred inbound auto-drain suppressed after claim release (auto_drain_after_release=False) for tenant_id=el-fogon-colombiano customer_key=+573223454241
INFO:     127.0.0.1:58636 - "POST /webhooks/twilio/whatsapp HTTP/1.1" 200 OK
```

The `200 OK` for B1's A appears after B1's single suppression log; the `200 OK` for B2's
A appears after both of B2's suppression logs. In both cases this confirms the drain
(including sequential replay LLM calls) ran synchronously in the `finally` block before
the HTTP response was returned. B1 contributes 1 suppression line; B2 contributes exactly
2 suppression lines (one per replayed deferred message). Total in log: 3.

### Constraints confirmed

| Constraint | Status |
| --- | --- |
| No app code changes during smoke | ✓ |
| No docs changes during smoke | ✓ |
| No commits or pushes during smoke | ✓ |
| No M9.6E implementation during smoke | ✓ |
| No manual drain invoked during B1 or B2 | ✓ |
