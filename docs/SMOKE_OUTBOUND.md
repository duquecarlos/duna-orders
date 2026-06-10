# Manual Outbound WhatsApp Acknowledgement Smoke

Purpose: send exactly one real WhatsApp acknowledgement to the operator's own
number for one already-confirmed order, using a throwaway Neon branch.

Do not run this smoke against production or the keeper runtime database. Do not
automate real sends. Keep outbound disabled by default outside this manual run.

## Latest Result

Status: passed.

Manual outbound smoke passed on a throwaway Neon branch. Alembic upgraded
successfully to head `a4b7c9d2e6f1`, and preflight passed with
`SUMMARY: PASS (15/15 checks passed)`.

Initial diagnostic attempts failed safely:

* first attempt failed with Twilio `20003` because credentials were placeholder
  or incorrect;
* second attempt failed with Twilio `21910` because the WhatsApp From/To channel
  pair was invalid.

The channel-pair issue was fixed by
`c769dae fix(outbound): normalize WhatsApp recipient addresses`. When the sender
starts with `whatsapp:`, the adapter sends a plain E.164 customer phone snapshot
to Twilio as `whatsapp:+...` without mutating stored order or customer data.

After joining the Twilio WhatsApp Sandbox again and using fresh confirmed order
`demo_ord_01486`, the real WhatsApp acknowledgement arrived.

Successful outbound row:

```text
outbound_message_id=out_01ktr15dq66n1q6x3v8atdwz6f
tenant_id=el-fogon-colombiano
order_id=demo_ord_01486
acknowledgement_type=order_confirmed_ack
status=sent
provider=twilio
provider_message_id=<populated>
attempt_count=1
last_error_code=null
last_error_message=null
sent_at=<populated>
```

Duplicate suppression check passed:

```text
outcome=suppressed_duplicate
reason=Acknowledgement was already sent.
attempted=False
sent=False
```

The duplicate attempt reused the same `outbound_message_id`, left status as
`sent`, kept `provider_message_id` populated, kept `attempt_count=1`, created no
second row, and produced no second send side effect.

The local `.env` was reset after the smoke:

```text
DUNA_STORAGE_BACKEND=memory
DUNA_OUTBOUND_ENABLED=false
```

The throwaway Neon branch is being kept temporarily and will auto-delete later.

## Manual Acknowledgement UI Result

Status: passed.

M8.6.1B added the operator-triggered manual acknowledgement UI in Orders Today.
The acknowledgement section renders only for confirmed orders. When outbound
setup is unavailable, the UI shows a safe reason and does not call the service.
When setup is available, the UI shows `Send acknowledgement`; the service call
happens only on explicit button click. Results are mapped through the UI-safe
outcome mapper and displayed by severity. The UI does not show
`provider_message_id`, provider error codes, provider error messages, Twilio
SIDs, auth tokens, or other provider internals.

Implementation commits:

* `feat(outbound): add acknowledgement UI result mapping`
* `a669fdd feat(outbound): add acknowledgement UI service setup`
* `a872131 feat(outbound): add manual acknowledgement UI`

Local safety smoke passed with default local safety settings:

```text
DUNA_STORAGE_BACKEND='memory'
DUNA_OUTBOUND_ENABLED=False
DUNA_OUTBOUND_TENANT_ID='el-fogon-colombiano'
TWILIO_WHATSAPP_FROM='whatsapp:+14155238886'
```

Twilio SID and token were set locally, but outbound was disabled. Orders Today
loaded with a memory/local confirmed test order. The confirmed card showed
`Acknowledgement` and the safe message `Outbound acknowledgement is disabled.`
The visible buttons were `Start preparation`, `Cancel`, and `Refresh`.
`Send acknowledgement` was not present, no provider internals were visible, and
no send path was available. Streamlit was stopped, local env remained or was
reset to memory/outbound disabled, focused tests reported `62 passed`, ruff
passed, and git status was clean.

An initial Postgres duplicate-suppression UI smoke attempt failed safely. The
known already-sent smoke order `demo_ord_01486` was confirmed but not visible in
Orders Today because `created_at=2026-05-27` and Orders Today filtered for
`2026-06-10`. Existing row `out_01ktr15dq66n1q6x3v8atdwz6f` remained unchanged:
`status=sent`, `provider=twilio`, `provider_message_id` populated,
`attempt_count=1`, no error fields, and populated `sent_at`. No UI click
occurred, no service/send attempt occurred, and no second WhatsApp send
happened.

Postgres UI duplicate-suppression smoke then passed on the throwaway Neon smoke
branch using process environment only. No secrets were printed. A guard check
confirmed the prior throwaway smoke row for `demo_ord_01486` existed.

Seeded today-visible confirmed order:

```text
order_id=ord_ui_dup_smoke_20260610
tenant_id=el-fogon-colombiano
status=confirmed
created_at=2026-06-10 06:45:09.634329+00
```

Seeded sent outbound acknowledgement row:

```text
outbound_message_id=out_01ktr4e71rw6hqeadbyb5dwgq7
acknowledgement_type=order_confirmed_ack
status=sent
provider=twilio
provider_message_id=<populated fake smoke value>
attempt_count=1
last_error_code=null
last_error_message=null
sent_at=<populated>
```

Orders Today showed the seeded order and the buttons `Send acknowledgement`,
`Start preparation`, `Cancel`, and `Refresh`. Clicking `Send acknowledgement`
once displayed:

```text
Acknowledgement was already sent.
```

After the click, the outbound row count for the tenant/order/type remained
`1`, the same `outbound_message_id` remained `sent`, `attempt_count` stayed
`1`, and no error fields were populated. No new WhatsApp send happened.
Streamlit was stopped and local environment was reset to:

```text
DUNA_STORAGE_BACKEND=memory
DUNA_OUTBOUND_ENABLED=false
```

Focused tests reported `62 passed`, ruff passed, and git status was clean.

## Manual Acknowledgement Status UI Result

Status: passed.

M8.6.1C added read-only outbound acknowledgement status visibility to Orders
Today for confirmed orders. The status display is only a hint; backend
claim-before-send remains the final send authority, and the button still routes
through `OutboundAcknowledgementService.send_order_confirmed_acknowledgement(...)`.

Orders Today now renders:

* no outbound row: `No acknowledgement has been sent yet.` with
  `Send acknowledgement` visible;
* sent row: `Acknowledgement was already sent.` with the send button hidden;
* `sending` or `send_requested`: `Acknowledgement is being sent.` with the send
  button hidden;
* unknown or may-have-sent: `Acknowledgement status is unclear — it may already have been sent. Check before taking any action.`
  with the send button hidden;
* failed retryable: `Acknowledgement could not be sent. Retry is not available yet.`
  with the send button hidden;
* blocked or missing required details:
  `Acknowledgement cannot be sent — order is missing required details.` with
  the send button hidden.

Manual Streamlit smoke passed:

* disabled/outbound-off confirmed order showed
  `Outbound acknowledgement is disabled.` and no `Send acknowledgement` button;
* sent existing-row order `ord_ui_dup_smoke_20260610` with outbound row
  `out_01ktr4e71rw6hqeadbyb5dwgq7` showed
  `Acknowledgement was already sent.` and no send button;
* no-record confirmed order `ord_ui_no_record_smoke_20260610` had
  `OUTBOUND_ACK_ROW_COUNT 0`, showed `No acknowledgement has been sent yet.`,
  and showed `Send acknowledgement`.

M8.6.1C verification passed:

```text
pytest -q -> 481 passed, 23 deselected
ruff check src tests -> passed
python -m compileall src tests -> passed
git diff --check -> only LF-to-CRLF warnings
```

## Provider-Neutral Unavailable UI Result

Status: passed.

M8.6.1D removed provider-specific details from Orders Today acknowledgement
unavailable/not-ready messages. Provider-specific setup diagnostics remain
internal, but Orders Today now renders:

* outbound disabled: `Outbound acknowledgement is disabled.`
* enabled but not fully configured:
  `Outbound acknowledgement is not fully configured.`

No send behavior, adapter behavior, preflight behavior, parser behavior,
`StorageInterface`, or `OrderService` coupling changed.

M8.6.1D verification passed:

```text
targeted tests -> 56 passed
pytest -q -> 489 passed, 23 deselected
ruff check src tests -> passed
python -m compileall src tests -> passed
git diff --check -> only LF-to-CRLF warnings
```

## Retry Acknowledgement UI Result

Status: passed.

M8.6.3B added the guarded `Retry acknowledgement` UI in Orders Today. Retry is
shown only for outbound acknowledgement rows with `status=failed`. Non-retryable
states remain gated: `sent`, `sending`, `send_requested`, `unknown`, no-record,
blocked/missing-detail, and disabled/not-ready states do not show retry.

Failed rows now render:

```text
Acknowledgement was not sent. You can retry.
Retry acknowledgement
```

The first click does not retry or send. It opens explicit confirmation only:

```text
Send this acknowledgement again? The previous attempt failed.
```

Confirmed retry routes through
`OutboundAcknowledgementService.send_order_confirmed_acknowledgement(..., retry_failed=True)`.
The UI does not call provider adapters directly, does not create outbound rows,
and continues to rely on backend claim/idempotency as the final send authority.
Post-action rerun/re-query prevents stale failed/retryable state from
persisting. Retry-goes-unknown hides retry.

DB-only smoke helper prepared a failed-row smoke order on the throwaway Neon
branch without calling the service or Twilio:

```text
ORDER_ID=ord_ui_retry_failed_smoke_20260610
CUSTOMER_NAME=Retry UI Smoke
ORDER_STATUS=confirmed
OUTBOUND_MESSAGE_ID=out_ui_retry_failed_smoke_20260610
OUTBOUND_STATUS=failed
OUTBOUND_ACK_ROW_COUNT=1
SERVICE_SEND_PATH_CALLED=false
TWILIO_CALLED=false
```

Manual Streamlit smoke passed. The failed row showed the failed text and
`Retry acknowledgement`. Clicking retry once showed only the confirmation text.
Final confirmation was not required for this UI-gate smoke.

Regression checks passed:

* sent row did not show retry;
* no-record row still showed `Send acknowledgement`, not
  `Retry acknowledgement`.

M8.6.3B verification passed:

```text
targeted tests -> 89 passed
pytest -q -> 502 passed, 23 deselected
ruff check src tests pages -> passed
python -m compileall src tests pages -> passed
git diff --check -> only LF-to-CRLF warnings
```

Resolved unrelated issue: during manual smoke setup, `pages/1_New_Order.py`
was observed to crash when `st.session_state.catalog_ready` was missing. This
was not caused by M8.6.1C/D; those slices only changed Orders Today outbound
acknowledgement display. M8.6.2A added the missing New Order session-state
initialization guard.

Important constraints remain unchanged: Twilio API acceptance is not delivery or
read callback proof; delivery/read callbacks, queue/worker behavior, retry-limit
policy, auto-send on confirm, and payment-dependent content remain deferred.

## 1. Prepare Throwaway Neon Branch

Create a throwaway Neon branch, for example:

```text
smoke-outbound-YYYY-MM-DD
```

Use only this branch for the smoke. Delete it after evidence is captured.

## 2. Configure Local Environment

Set local shell variables or `.env` values for the throwaway branch only:

```powershell
$env:DUNA_STORAGE_BACKEND="postgres"
$env:DATABASE_URL="postgresql+psycopg://..."
$env:DUNA_OUTBOUND_ENABLED="true"
$env:DUNA_OUTBOUND_TENANT_ID="el-fogon-colombiano"
$env:TWILIO_ACCOUNT_SID="AC..."
$env:TWILIO_AUTH_TOKEN="..."
$env:TWILIO_WHATSAPP_FROM="whatsapp:+14155238886"
```

Use the Twilio WhatsApp sender approved for the account or sandbox. Send only to
your own WhatsApp number by choosing or creating a confirmed test order whose
customer phone snapshot is your own test number.

For Twilio Sandbox smoke, the recipient number must be joined to the sandbox
before sending. If the recipient has left or the sandbox session expires, join
the sandbox again before running the smoke.

The confirmed order may store the customer phone as plain E.164, such as
`+573001112233`. When `TWILIO_WHATSAPP_FROM` starts with `whatsapp:`, the
outbound Twilio adapter sends the recipient to Twilio as `whatsapp:+...` without
mutating the stored order or customer snapshot.

## 3. Preflight

Run the read-only preflight:

```powershell
python scripts/smoke_preflight.py
```

Resolve every `FAIL`. The outbound checks must show:

```text
PASS: DUNA_OUTBOUND_ENABLED - enabled
PASS: DUNA_OUTBOUND_TENANT_ID present - configured
PASS: TWILIO_WHATSAPP_FROM present - configured
PASS: TWILIO_ACCOUNT_SID present - configured
PASS: TWILIO_AUTH_TOKEN present for outbound - configured
```

No network call to Twilio is made by preflight.

## 4. Upgrade Throwaway Branch

If preflight reports the database is behind head, upgrade only the throwaway
branch:

```powershell
alembic upgrade head
alembic current
```

Confirm the `outbound_messages` table has `provider_message_id`:

```sql
select column_name
from information_schema.columns
where table_name = 'outbound_messages'
order by ordinal_position;
```

## 5. Prepare Confirmed Test Order

Ensure there is one confirmed order for `DUNA_OUTBOUND_TENANT_ID` and that its
`customer_phone_snapshot` is your own WhatsApp test number.

Do not use a real customer number. Do not rely on auto-send; outbound remains an
explicit operator-triggered action.

## 6. Send One Acknowledgement

Run the guarded manual smoke script:

```powershell
python scripts/send_outbound_ack_smoke.py `
  --tenant-id "$env:DUNA_OUTBOUND_TENANT_ID" `
  --order-id "ord_test_confirmed" `
  --requested-by "operator-smoke" `
  --business-name "El Fogon"
```

Expected successful output includes:

```text
outcome=sent
reason=Acknowledgement sent.
attempted=True
sent=True
status=sent
provider_message_id=SM...
```

## 7. Verify Database Row

In Neon, verify the outbound row:

```sql
select outbound_message_id,
       tenant_id,
       order_id,
       acknowledgement_type,
       status,
       provider,
       provider_message_id,
       attempt_count,
       sent_at
from outbound_messages
where tenant_id = 'el-fogon-colombiano'
  and order_id = 'ord_test_confirmed'
  and acknowledgement_type = 'order_confirmed_ack';
```

Expected:

* one row exists;
* `status = 'sent'`;
* `provider_message_id` is populated;
* the idempotency key is `tenant_id + order_id + acknowledgement_type`;
* `attempt_count = 1` for the first successful send.

## 8. Verify WhatsApp Receipt

Confirm the WhatsApp message arrived on your own test phone.

The service result and row status mean Twilio accepted the message. They do not
prove delivery or read status.

## 9. Duplicate Suppression Check

Run the same command again with the same tenant and order id.

Expected:

```text
outcome=suppressed_duplicate
attempted=False
sent=False
```

Verify:

* no second WhatsApp message arrives;
* no second `outbound_messages` row exists for the same idempotency key;
* `attempt_count` did not increase.

## 10. Optional Failure/Unknown Checks

Unit tests cover timeout, 5xx, definitive rejection, and auth error mapping.
Only run manual failure checks if the throwaway branch and Twilio sandbox setup
can do so without sending repeated messages to a real customer.

Timeouts and Twilio 5xx map to `unknown`, which is non-resendable by default.
Do not retry unknown states until you verify out-of-band whether the customer may
have received the message.

## 11. Teardown

Reset local environment to runtime-safe values:

```powershell
Remove-Item Env:\DUNA_OUTBOUND_ENABLED -ErrorAction SilentlyContinue
Remove-Item Env:\DUNA_OUTBOUND_TENANT_ID -ErrorAction SilentlyContinue
Remove-Item Env:\TWILIO_ACCOUNT_SID -ErrorAction SilentlyContinue
Remove-Item Env:\TWILIO_AUTH_TOKEN -ErrorAction SilentlyContinue
Remove-Item Env:\TWILIO_WHATSAPP_FROM -ErrorAction SilentlyContinue
Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:\DUNA_STORAGE_BACKEND -ErrorAction SilentlyContinue
```

Restore `.env` to memory/default safe values. Delete the throwaway Neon branch.
