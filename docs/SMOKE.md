# Local Webhook Smoke Runbook

This runbook is for the manual local plus tunnel smoke for the Twilio WhatsApp inbound webhook. Do not put real secret values in this file or terminal output shared in docs.

Use [docs/SMOKE_CHECKLIST.md](SMOKE_CHECKLIST.md) as the execution checklist and pass/fail sheet for the manual run.

For the separate manual outbound acknowledgement smoke, use
[docs/SMOKE_OUTBOUND.md](SMOKE_OUTBOUND.md). Do not combine inbound and outbound
smokes unless that is the explicit run plan.

## 1. Prepare Throwaway Neon Branch

Use a throwaway Neon branch as the default smoke target. Do not run the inbound smoke against the keeper or production branch unless that is the explicit purpose of the run.

Create a branch named for the smoke run, for example:

```text
smoke-inbound-YYYY-MM-DD
```

Set local configuration for the throwaway branch before starting the app:

```powershell
$env:DATABASE_URL="postgresql://..."
$env:DUNA_STORAGE_BACKEND="postgres"
$env:WEBHOOK_TENANT_ID="el-fogon-colombiano"
$env:TWILIO_AUTH_TOKEN="..."
```

`TWILIO_AUTH_TOKEN` must be the real Twilio Account Auth Token. A Messaging Service SID, API key secret, sandbox value, or other Twilio token will fail signature validation with `403`.

For the seeded demo catalog smoke, `WEBHOOK_TENANT_ID` must be `el-fogon-colombiano`. Using `WEBHOOK_TENANT_ID=default` can still return `200` and write `processed_messages` plus `parse_log`, but no order is created if the parsed items cannot resolve against that tenant catalog.

Also confirm `ANTHROPIC_API_KEY` is configured before sending the happy-path message. The preflight does not currently check it.

## 2. Preflight

Run the read-only preflight and resolve every `FAIL` before continuing:

```powershell
python scripts/smoke_preflight.py
```

The script checks local configuration, database connectivity, and Alembic revision state. If it reports the database is behind head, it prints:

```powershell
alembic upgrade head
```

Do not manufacture an initial preflight failure. Run preflight against the throwaway branch and fix any real `FAIL` lines.

## 3. Upgrade Throwaway Branch If Needed

After reviewing the preflight output, manually upgrade only the throwaway Neon branch to head if it is behind:

```powershell
alembic upgrade head
```

Confirm the live database revision is exactly `d2f7b8a4c901`:

```powershell
alembic current
```

Do not upgrade the keeper branch as part of this smoke unless that has been separately approved.

## 4. Start FastAPI Locally

Use the Postgres backend and run the webhook app:

```powershell
$env:DUNA_STORAGE_BACKEND="postgres"
uvicorn duna_orders.web.app:app --host 127.0.0.1 --port 8000 --reload
```

Leave this process running and watch logs.

Restart Uvicorn after any `.env` or shell environment change that affects the app, including `DATABASE_URL`, `TWILIO_AUTH_TOKEN`, `TWILIO_WEBHOOK_PUBLIC_URL`, `WEBHOOK_TENANT_ID`, `DUNA_STORAGE_BACKEND`, or parser configuration.

## 5. Start Cloudflared Quick Tunnel

In a second terminal:

```powershell
cloudflared tunnel --url http://127.0.0.1:8000
```

Cloudflared quick tunnels generate a new public host every time they start. Copy the generated `https://...trycloudflare.com` tunnel URL for this run only.

If the quick tunnel is restarted, the old URL is dead. Update `TWILIO_WEBHOOK_PUBLIC_URL`, restart Uvicorn, update the Twilio sandbox inbound URL, rerun preflight, and recheck that an unsigned public POST returns `403`.

## 6. Configure Twilio Webhook URL

The FastAPI route for inbound Twilio WhatsApp messages is exactly:

```text
POST /webhooks/twilio/whatsapp
Content-Type: application/x-www-form-urlencoded
```

The Twilio URL must match the FastAPI route exactly, including trailing slash behavior. `/webhooks/twilio/whatsapp` is not the same as `/webhooks/twilio/whatsapp/`. FastAPI may redirect on a slash mismatch, and that can break Twilio signature validation because the URL used for HMAC validation no longer matches exactly.

Set the local environment URL to the tunnel host plus the exact webhook path:

```powershell
$env:TWILIO_WEBHOOK_PUBLIC_URL="https://YOUR-TUNNEL.trycloudflare.com/webhooks/twilio/whatsapp"
```

Set the same exact URL in the Twilio sandbox console for inbound WhatsApp messages:

```text
https://YOUR-TUNNEL.trycloudflare.com/webhooks/twilio/whatsapp
```

Restart FastAPI after changing the environment variable.

For Twilio Sandbox setup, first join the sandbox from WhatsApp. Then configure **Sandbox settings > When a message comes in** with the exact URL above and method `POST`.

The required ordering for the real smoke is:

1. Start the local FastAPI app.
2. Start the cloudflared tunnel.
3. Copy the generated HTTPS host.
4. Configure `TWILIO_WEBHOOK_PUBLIC_URL` using that host plus `/webhooks/twilio/whatsapp`.
5. Configure the same exact URL in the Twilio WhatsApp sandbox console.
6. Restart Uvicorn so the app reads the updated environment.
7. Rerun preflight.
8. Recheck that a public unsigned POST returns `403`.
9. Only then send the test WhatsApp message.

## 7. Send Test WhatsApp Order

From the WhatsApp number joined to the Twilio sandbox, send a realistic Colombian-Spanish order, for example:

```text
Buenas, quiero pedir 2 bandejas paisas, una sin chicharron, y 1 limonada de panela. Es para domicilio en Chapinero y pago por Nequi.
```

## 8. Verify Results

Check FastAPI logs for a signed Twilio request returning `200`.

In Neon, verify:

```sql
select order_id, tenant_id, status, raw_message, created_at
from orders
order by created_at desc
limit 5;

select message_sid, from_number, raw_body, resulting_order_id, received_at
from processed_messages
order by received_at desc
limit 5;

select transition_id, tenant_id, order_id, from_status, to_status, source, occurred_at
from order_status_transitions
order by occurred_at desc
limit 5;
```

Expected result: one draft order, one processed message row linked to that order, and one `NULL -> draft` lifecycle transition with `source = system`.

Also verify that `processed_messages.raw_body` captured the full inbound body, `processed_messages.resulting_order_id` points at the created order, and `parse_log` captured the successful parser output.

## 9. Idempotency Check

Use Twilio retry tooling or resend the same captured webhook payload with the same `MessageSid` and valid signature. Verify:

* the endpoint returns `200`;
* no second order is created;
* the existing `processed_messages` row remains the idempotency record;
* no duplicate `order_status_transitions` row is appended for the duplicate delivery.

## 10. Signature Rejection Check

Send a POST to `/webhooks/twilio/whatsapp` with a missing or tampered `X-Twilio-Signature`. The expected result is `403` and no row count changes in `orders`, `processed_messages`, `order_status_transitions`, or `parse_log`.

## 11. Teardown

Stop the local FastAPI process.

Stop the `cloudflared` process.

Remove or replace the Twilio sandbox inbound webhook URL so it no longer points at the temporary tunnel.

Clear local shell variables that contain live configuration:

```powershell
Remove-Item Env:\TWILIO_WEBHOOK_PUBLIC_URL -ErrorAction SilentlyContinue
Remove-Item Env:\DUNA_STORAGE_BACKEND -ErrorAction SilentlyContinue
```

Restore local `.env` values if they were changed for the smoke. In particular, replace any throwaway `DATABASE_URL` and any dead quick-tunnel `TWILIO_WEBHOOK_PUBLIC_URL`.

Let the throwaway Neon branch auto-delete or delete it manually after evidence is captured, according to the run plan.
