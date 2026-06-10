# M8.6 Outbound Acknowledgement Design

Status: approved design only.

Base: `53a7f03 refactor(storage): mark product and customer reads unscoped`

M8.6 closes the visible customer loop after the operator confirms an inbound
order. The first outbound capability is deliberately narrow: one
operator-triggered transactional acknowledgement for one already-confirmed
order.

No implementation, migration, test, schema change, or Twilio send wiring is
part of this document pass.

## 1. Context

The inbound spine is complete and hardened:

* inbound WhatsApp webhook;
* Twilio signature validation;
* `MessageSid` idempotency through Postgres `processed_messages`;
* parse to draft;
* operator review;
* approve;
* atomic `approved -> confirmed` inventory commit through
  `OrderService.confirm_approved_order(...)`.

M8.3 explicitly separated outbound messaging from inventory confirmation.
M8.6 keeps that separation. Confirming inventory does not send a customer
message automatically.

Current repo facts:

* Inbound Twilio configuration includes `twilio_auth_token`,
  `twilio_webhook_public_url`, and `webhook_tenant_id`.
* No outbound Twilio REST send capability exists yet.
* There is an existing deterministic UI-only confirmation-message generator in
  `src/duna_orders/ui/confirmation_message.py`.
* There is no tenant-bound outbound sender identity config yet.
* Current inbound tenant resolution is single-tenant via `webhook_tenant_id`.
* `processed_messages` is Postgres-only and remains the inbound idempotency
  store.

## 2. Decision Summary

* Do not auto-send on confirm.
* First trigger is explicit operator action: review exact text, then send.
* First message content is deterministic Colombian Spanish.
* No LLM-generated outbound text.
* Only confirmed orders can be acknowledged.
* Outbound send is outside the inventory transaction.
* Twilio failure never rolls back inventory.
* Inventory failure never creates or sends an acknowledgement.
* First sender identity is env-gated single-tenant pilot config.
* First real-send backend support is Postgres-only.
* Delivery/read callbacks are deferred.

## 3. Trigger

First-slice trigger:

An operator explicitly clicks a "notify customer" action for an already
confirmed order.

The UI must show the exact acknowledgement body before send. First slice should
support review/send, not free-form editing.

Rationale:

* This is the first real customer-facing send path.
* Human-in-the-loop control matters more than speed.
* The operator should see customer-facing text before it leaves the system.
* Outbound send must not become part of the inventory-confirmation action.
* A double-click, rerender, retry, or page refresh must not duplicate a
  customer message.

Rejected for first slice:

* auto-send on `approved -> confirmed`;
* LLM-generated outbound copy;
* free-form edited outbound copy;
* queue/worker dispatch.

## 4. Message Content

Use a deterministic Colombian Spanish template.

Inputs:

* customer display name from customer record or order snapshot;
* order items;
* item modifications;
* total;
* payment method if present;
* fulfillment type;
* delivery zone or delivery address if present;
* tenant/business display name when available.

Sample:

```text
Hola Carlos, tu pedido quedó confirmado.

Pedido:
- 1x Bandeja paisa (sin aguacate)
- 2x Limonada natural

Total: $58.000
Pago: Nequi
Entrega: Chapinero

Gracias por pedir en El Fogón Colombiano.
```

The text is a transactional acknowledgement. It does not verify payment, request
payment, promise exact delivery time, or mention media/comprobantes.

## 5. Idempotency Model

Outbound acknowledgement idempotency mirrors the M8.1.4 inbound dedup posture:
provider retries and UI retries must not duplicate customer-visible effects.

Idempotency key:

```text
tenant_id + order_id + acknowledgement_type
```

Initial acknowledgement type:

```text
order_confirmed_ack
```

Database constraint:

Add a unique constraint on:

```text
tenant_id, order_id, acknowledgement_type
```

This constraint is load-bearing. It prevents duplicate acknowledgement rows for
the same tenant/order/type even under double-clicks, refreshes, or concurrent
operator actions.

Recommended outbound record fields:

* `outbound_message_id`;
* `tenant_id`;
* `order_id`;
* `acknowledgement_type`;
* `to_number`;
* `from_number`;
* `body`;
* `status`;
* `provider`;
* `provider_message_id`;
* `attempt_count`;
* `last_error_code`;
* `last_error_message`;
* `requested_by`;
* `created_at`;
* `updated_at`;
* `sent_at`.

Status model:

* `send_requested`
* `sending`
* `sent`
* `failed`
* `unknown`

Status semantics:

* `send_requested` and `sending` are in-progress states. They are
  non-retryable and non-duplicating.
* `sent` means the provider accepted the message and returned a successful
  send result. It does not mean delivered, read, or acted on by the customer.
* `failed` means the send failed in a known way and can be retried explicitly.
* `unknown` means the system cannot safely determine whether the provider
  accepted the send. It is non-retryable by default because duplicate customer
  messages are worse than silence.

Retry rules:

* A retry never creates a second row for the same key.
* Retries only reuse the existing `failed` row.
* Retrying increments `attempt_count` and updates status/error fields on the
  existing row.
* `send_requested`, `sending`, `sent`, and `unknown` suppress additional sends.
* Unknown/ambiguous responses fail safe: do not send again automatically; show
  the state to the operator.

## 6. Decoupling Boundary

Inventory confirmation and outbound acknowledgement are separate operations.

`OrderService.confirm_approved_order(...)`:

* validates tenant/order/status;
* commits inventory atomically;
* appends lifecycle transition;
* returns confirmed order;
* does not create outbound records;
* does not call Twilio.

Outbound acknowledgement service:

* starts only after an order is already confirmed;
* reads already-committed order state;
* validates order belongs to `tenant_id`;
* validates order status is `confirmed`;
* creates or reuses the outbound acknowledgement row;
* sends through a provider adapter only when idempotency state allows it;
* updates outbound status after the provider call.

Coupling is unsafe because:

* external provider calls are slow and failure-prone;
* database transaction retry semantics conflict with provider-send retry
  semantics;
* Twilio failure must not roll back committed inventory;
* inventory failure must not send a customer acknowledgement;
* ambiguous provider response must not create ambiguous inventory state.

## 7. Tenant and Sender Identity

First sender identity is env-gated single-tenant pilot config.

Required first-slice config shape:

* Twilio account/auth credentials for outbound send;
* configured WhatsApp sender/from number;
* configured tenant id that this sender is allowed to serve;
* explicit outbound enabled flag or mode.

The outbound service must validate:

* `tenant_id` is explicit;
* order exists for `tenant_id`;
* order status is `confirmed`;
* customer destination phone exists;
* configured sender identity exists;
* configured sender identity is allowed for this tenant.

Tenant-channel binding tables may come later. They are not required for the
first single-tenant pilot slice.

## 8. Backend Support

First real outbound support is Postgres-only.

Rationale:

* Durable idempotency is required before sending real customer messages.
* The unique constraint belongs in the database.
* M8.3 atomic confirmation is already Postgres-oriented.
* Sheets remains historical/legacy for webhook behavior and should not own
  real-send idempotency.

Memory support may exist only through fakes/unit tests. Google Sheets should
surface unsupported/unavailable state for real outbound sends.

Storage remains persistence. Twilio send orchestration belongs in the service
layer, not storage.

## 9. Operator UX and Failure Handling

Minimal operator UI:

* show confirmed order;
* show destination phone;
* show sender identity;
* show exact acknowledgement body;
* show current outbound status if a row already exists;
* provide an explicit send action only when allowed.

Success:

* show `sent` state;
* show provider message id if available;
* state clearly that this means provider accepted the message, not delivered or
  read.

Known failure:

* show `failed`;
* show readable error;
* allow explicit retry using the same existing failed row.

Unknown/ambiguous:

* show `unknown`;
* do not auto-retry;
* surface operator copy such as:
  "Estado incierto. No se reintentó automáticamente para evitar enviar el
  mensaje dos veces."

Blocked before send:

* missing customer phone;
* missing sender identity;
* tenant mismatch;
* order not confirmed;
* outbound disabled;
* unsupported backend;
* provider credentials missing.

The UI must not crash or silently duplicate sends.

## 10. Implementation Slices

### M8.6.1A - Service, Template, Persistence

* Deterministic acknowledgement template builder.
* Outbound acknowledgement service.
* Postgres outbound acknowledgement persistence.
* Unique constraint on `tenant_id + order_id + acknowledgement_type`.
* Provider adapter protocol with fake adapter in tests.
* Explicit operator-triggered service method.
* No UI.
* No real Twilio send in tests.

Required test themes for implementation slice:

* template rendering;
* only confirmed orders can be acknowledged;
* tenant mismatch protection;
* missing customer phone blocks send;
* missing sender identity blocks send;
* double-send suppression;
* in-progress statuses suppress duplicate sends;
* retry reuses existing failed row;
* unknown state suppresses resend;
* provider message id stored when available.

### M8.6.1B - Operator UI and Smoke

* Operator-triggered manual acknowledgement action in Orders Today.
* Send button renders only for confirmed orders when outbound setup is ready.
* Service call happens only on explicit operator click.
* Results are mapped through UI-safe outcome messages.
* Manual disabled-mode and duplicate-suppression UI smoke passed.
* No auto-send.
* No retry UI.
* Delivery/read callbacks still deferred.

### M8.6.1C - Read-Only Status Visibility

* Orders Today renders read-only acknowledgement state before the operator
  decides whether to send.
* No-row state shows `No acknowledgement has been sent yet.` and shows the send
  button.
* Sent, sending/send_requested, unknown/may-have-sent, failed, and blocked
  states hide the send button.
* UI status is display-only; backend claim-before-send remains the final send
  authority.
* Manual Streamlit smoke passed for disabled, sent existing-row, and no-record
  states.

### M8.6.1D - Provider-Neutral Unavailable UI

* Orders Today no longer renders provider-specific unavailable/not-ready setup
  details.
* Disabled still renders `Outbound acknowledgement is disabled.`
* Enabled but not fully configured renders
  `Outbound acknowledgement is not fully configured.`
* Provider-specific setup diagnostics remain internal.

### M8.6.3B - Retry Acknowledgement UI

* Orders Today renders `Retry acknowledgement` only for failed outbound
  acknowledgement rows.
* Failed rows render `Acknowledgement was not sent. You can retry.`
* The first retry click opens explicit confirmation only:
  `Send this acknowledgement again? The previous attempt failed.`
* Confirmed retry routes through
  `OutboundAcknowledgementService.send_order_confirmed_acknowledgement(..., retry_failed=True)`.
* The UI does not call provider adapters directly and does not create outbound
  rows.
* Backend claim/idempotency remains the final send authority.
* `sent`, `sending`, `send_requested`, `unknown`, no-record,
  blocked/missing-detail, and disabled/not-ready states do not show retry.
* Manual Streamlit UI-gate smoke passed using seeded failed-row order
  `ord_ui_retry_failed_smoke_20260610`.
* Retry-limit policy, `attempt_count` display, and last failure time display
  remain deferred.

### M8.6.3C - Guarded Retry Execution Smoke

* Smoke-only validation ran against the throwaway Neon branch with no code
  changes.
* Used a safe operator-controlled WhatsApp recipient ending in `4241`.
* Verified the real retry execution path from Orders Today UI through service,
  store, and Twilio.
* Confirmed retry reused outbound row
  `out_ui_retry_execution_smoke_20260610`.
* Confirmed row count stayed `1`, `attempt_count` increased from `1` to `2`,
  final status was `sent`, provider message id was populated, and sent timestamp
  was populated.
* Confirmed the WhatsApp message was received by the safe test recipient.
* No retry-limit policy, attempt-count display, failure-time display,
  delivery/read callbacks, queue/worker behavior, auto-send, or
  payment-dependent content was added.

### M8.6.3E - Retry Max-Attempt Enforcement

* Backend/store enforces a maximum of `2` total attempts per outbound
  acknowledgement row.
* Failed rows with `attempt_count >= 2` are suppressed with
  `suppressed_retry_limit_reached`, even when `retry_failed=True`.
* The service maps max-attempt suppression to:
  `Acknowledgement was not sent. Manual follow-up is required.`
* Orders Today shows `Retry acknowledgement` only for failed rows with
  `attempt_count < 2`.
* Orders Today renders failed rows with `attempt_count >= 2` as
  `Acknowledgement was not sent. Manual follow-up is required.` and hides retry.
* Max-attempt suppression does not call the adapter, does not create a new
  outbound row, and keeps row count at `1`.
* `attempt_count`, failure time, provider errors, and provider internals remain
  hidden in the UI.
* Manual UI smoke passed on the throwaway Neon branch for failed
  `attempt_count=1` and failed `attempt_count=2` rows.

## 11. Explicitly Out of Scope

* No auto-send on confirm.
* No LLM-generated outbound content.
* No free-form edit in first slice.
* No delivery/read callbacks.
* No queue/worker.
* No conversation state.
* No multi-turn follow-up handling.
* No inbound media/comprobante.
* No `payment_status` dependency.
* No parser behavior change.
* No `PROMPT_VERSION` change.
* No outbound send inside inventory transaction.
* No marketing or broadcast messaging.
* No payment request.
* No cancellation stock reversal.
* No duplicate-movement repair.
* No StorageInterface tenant-scoping evolution.

## 12. Future Interactions

Deferred future work may add:

* delivery/read status callbacks;
* richer tenant-channel binding;
* queue-backed dispatch;
* LLM-proposed non-commitment messages behind policy guards;
* operator-edited text with audit history;
* payment-dependent content;
* comprobante/media handling;
* richer outbound conversation state.

These future paths must preserve the M8.6 boundary: customer sends are durable,
idempotent, tenant-scoped, and not coupled to inventory transactions.
