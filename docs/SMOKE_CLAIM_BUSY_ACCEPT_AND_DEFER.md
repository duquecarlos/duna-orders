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
* **Accept-and-defer verification (M9.6D-fix-impl)**: **not yet runnable**.
  M9.6D-fix-impl (the `deferred_inbound` table, defer-write, `202` response,
  drain-on-release, sweep backstop - see
  `docs/M9_6D_ACCEPT_AND_DEFER_CLAIM_BUSY_DESIGN.md`) has not been
  implemented. Phase 3b and Phases 4-5 below document the procedure to run
  once it has.

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
| Phase 3b: claim-busy returns `202`, `deferred_inbound` row present, `sid_rows = 0` | NOT YET RUN (M9.6D-fix-impl not implemented) | |
| Phase 4-5: drain processes the deferred row, `processed_messages` row created, ordering preserved | NOT YET RUN (M9.6D-fix-impl not implemented) | |

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
