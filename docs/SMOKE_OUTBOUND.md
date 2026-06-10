# Manual Outbound WhatsApp Acknowledgement Smoke

Purpose: send exactly one real WhatsApp acknowledgement to the operator's own
number for one already-confirmed order, using a throwaway Neon branch.

Do not run this smoke against production or the keeper runtime database. Do not
automate real sends. Keep outbound disabled by default outside this manual run.

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
