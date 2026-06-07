# Local Webhook Smoke Runbook

This runbook is for the manual local plus tunnel smoke for the Twilio WhatsApp inbound webhook. Do not put real secret values in this file or terminal output shared in docs.

## 1. Preflight

Run the read-only preflight and resolve every `FAIL` before continuing:

```powershell
python scripts/smoke_preflight.py
```

The script checks local configuration, database connectivity, and Alembic revision state. If it reports the database is behind head, it prints:

```powershell
alembic upgrade head
```

## 2. Upgrade Neon

After reviewing the preflight output, manually upgrade the Neon database to head:

```powershell
alembic upgrade head
```

Confirm the live database revision is exactly `d2f7b8a4c901`:

```powershell
alembic current
```

## 3. Start FastAPI Locally

Use the Postgres backend and run the webhook app:

```powershell
$env:DUNA_STORAGE_BACKEND="postgres"
uvicorn duna_orders.web.app:app --host 127.0.0.1 --port 8000 --reload
```

Leave this process running and watch logs.

## 4. Start Cloudflared Quick Tunnel

In a second terminal:

```powershell
cloudflared tunnel --url http://127.0.0.1:8000
```

Cloudflared quick tunnels generate a new public host every time they start. Copy the generated `https://...trycloudflare.com` tunnel URL for this run only.

## 5. Configure Twilio Webhook URL

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

The required ordering for the real smoke is:

1. Start the local FastAPI app.
2. Start the cloudflared tunnel.
3. Copy the generated HTTPS host.
4. Configure `TWILIO_WEBHOOK_PUBLIC_URL` using that host plus `/webhooks/twilio/whatsapp`.
5. Configure the same exact URL in the Twilio WhatsApp sandbox console.
6. Only then send the test WhatsApp message.

## 6. Send Test WhatsApp Order

From the WhatsApp number joined to the Twilio sandbox, send a realistic Colombian-Spanish order, for example:

```text
Buenas, quiero pedir 2 bandejas paisas, una sin chicharron, y 1 limonada de panela. Es para domicilio en Chapinero y pago por Nequi.
```

## 7. Verify Results

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

## 8. Idempotency Check

Use Twilio retry tooling or resend the same captured webhook payload with the same `MessageSid` and valid signature. Verify:

* the endpoint returns `200`;
* no second order is created;
* the existing `processed_messages` row remains the idempotency record;
* no duplicate `order_status_transitions` row is appended for the duplicate delivery.

## 9. Teardown

Stop the local FastAPI process.

Stop the `cloudflared` process.

Remove or replace the Twilio sandbox inbound webhook URL so it no longer points at the temporary tunnel.

Clear local shell variables that contain live configuration:

```powershell
Remove-Item Env:\TWILIO_WEBHOOK_PUBLIC_URL -ErrorAction SilentlyContinue
Remove-Item Env:\DUNA_STORAGE_BACKEND -ErrorAction SilentlyContinue
```
