# Duna — Manual Inbound Smoke Execution Checklist

Base commit: `b364b3c`

Route under test: `POST /webhooks/twilio/whatsapp` (no trailing slash)

Use this checklist for the manual inbound smoke only. Record evidence from logs, Twilio, and Neon, but do not paste literal secrets into this file.

## Phase 0 — Pre-smoke setup

- [ ] Create a throwaway Neon branch for this smoke run.
- [ ] Set `DATABASE_URL` to the throwaway Neon branch, not the keeper branch.
- [ ] Confirm the working tree and branch are the intended smoke target.
- [ ] Confirm the local environment uses the Postgres backend for the app.
- [ ] Run preflight against the throwaway branch before setup changes:

```powershell
python scripts/smoke_preflight.py
```

- [ ] Preflight must run during setup and show at least one real `FAIL` before the setup is corrected.
- [ ] Correct only the intended setup issue or issues.
- [ ] Re-run preflight and confirm it no longer reports setup-blocking `FAIL` lines.
- [ ] Evidence captured:

## Phase 1 — Bring up infrastructure (order matters)

- [ ] Start FastAPI locally.

```powershell
$env:DUNA_STORAGE_BACKEND="postgres"
uvicorn duna_orders.web.app:app --host 127.0.0.1 --port 8000 --reload
```

- [ ] Start a cloudflared quick tunnel to the local FastAPI app.

```powershell
cloudflared tunnel --url http://127.0.0.1:8000
```

- [ ] Copy the new `trycloudflare.com` host from cloudflared output.
- [ ] Set `TWILIO_WEBHOOK_PUBLIC_URL` to the new host with the exact route under test:

```powershell
$env:TWILIO_WEBHOOK_PUBLIC_URL="https://<host>.trycloudflare.com/webhooks/twilio/whatsapp"
```

- [ ] Restart FastAPI if `TWILIO_WEBHOOK_PUBLIC_URL` was set after the app started.
- [ ] Configure the Twilio WhatsApp sandbox inbound webhook to the exact same URL.
- [ ] Set Twilio method to `POST`.
- [ ] Send requests as `application/x-www-form-urlencoded`.
- [ ] Confirm the path is exactly `/webhooks/twilio/whatsapp`, with no trailing slash.
- [ ] Confirm the Twilio sandbox URL matches `TWILIO_WEBHOOK_PUBLIC_URL` character-for-character.
- [ ] Evidence captured:

## Phase 2 — Happy path

- [ ] Send one realistic WhatsApp order from the joined Twilio sandbox number.
- [ ] Confirm the first delivery returns `2xx`.
- [ ] Capture and note the `MessageSid`:
- [ ] Verify exactly one row exists in `orders` for this inbound message.
- [ ] Verify exactly one row exists in `processed_messages` for the captured `MessageSid`.
- [ ] Verify `processed_messages.resulting_order_id` points to the created order.
- [ ] Verify at least one row exists in `order_status_transitions` for the created order.
- [ ] Evidence captured:

## Phase 3 — Duplicate MessageSid

- [ ] Do not send a new WhatsApp message and treat it as a duplicate. A new WhatsApp message creates a new `MessageSid` and is not a duplicate test.
- [ ] Exercise duplicate handling through Twilio retry behavior or by manually replaying the captured POST payload with the same `MessageSid` and a valid Twilio signature.
- [ ] Confirm the duplicate delivery returns the expected non-error response.
- [ ] Verify `processed_messages` still has exactly one row for that `MessageSid`.
- [ ] Verify `processed_messages.resulting_order_id` still points to the original order.
- [ ] Verify no second order is created.
- [ ] Verify no duplicate transition exists for a phantom second order.
- [ ] Evidence captured:

## Phase 4 — Signature rejection

- [ ] Send a POST to `/webhooks/twilio/whatsapp` with a missing or tampered `X-Twilio-Signature`.
- [ ] Confirm the response is `4xx`, not `2xx`.
- [ ] Verify no new rows were inserted into `orders`.
- [ ] Verify no new rows were inserted into `processed_messages`.
- [ ] Verify no new rows were inserted into `order_status_transitions`.
- [ ] Evidence captured:

## Phase 5 — Teardown

- [ ] Stop the cloudflared tunnel.
- [ ] Stop the local FastAPI app.
- [ ] Remove or replace the Twilio sandbox inbound webhook URL so it no longer points at the temporary tunnel.
- [ ] Delete the throwaway Neon branch.
- [ ] Point local configuration back to the keeper branch.
- [ ] Run preflight/current check against the keeper branch.
- [ ] Only run `alembic upgrade head` on keeper if it is intentionally behind and the target DB is confirmed.
- [ ] Evidence captured:

## Smoke Verdict Table

| Check | Result | Evidence |
| --- | --- | --- |
| Preflight gate tripped during setup | PASS / FAIL | |
| Happy path | PASS / FAIL | |
| Duplicate MessageSid | PASS / FAIL | |
| Signature rejection | PASS / FAIL | |

`GREEN` = all four pass.

Anything else = stop, diagnose, do not proceed to outbound/M9 scope.

## Notes / Observations

```text

```
