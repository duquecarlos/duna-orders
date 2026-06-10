# M8.6.3A Retry UI Safety Design

Status: design policy only.

Baseline: `8f3eda1 docs(ui): close new order session-state fix`

M8.6.3A does not implement retry UI. It defines the safety contract for a
future operator-triggered `Retry acknowledgement` button. Retry is the first
outbound UI action that can cause a second send attempt to a real customer, so
the UI must stay conservative and the backend claim/idempotency path must remain
the final authority.

## Pre-flight Contract Findings

Current service method:

```python
OutboundAcknowledgementService.send_order_confirmed_acknowledgement(
    *,
    tenant_id: str,
    order_id: str,
    from_number: str,
    requested_by: str,
    business_name: str | None = None,
    retry_failed: bool = False,
) -> OutboundAcknowledgementResult
```

When `retry_failed=True`, the service still performs all normal precondition
checks before any send attempt:

* tenant id is required;
* order id is required;
* sender phone number is required;
* operator identity is required;
* the order is read through the tenant-scoped order reader;
* the order must exist for the tenant;
* the order must be `confirmed`;
* customer phone must be present.

After those checks, the service always routes through:

```python
OutboundAcknowledgementStore.claim_order_acknowledgement_for_send(...)
```

with `retry_failed=retry_failed`. The UI/service path does not call the provider
adapter directly. The adapter boundary is:

```python
OutboundMessageAdapter.send_message(
    *,
    from_number: str,
    to_number: str,
    body: str,
) -> OutboundProviderResult
```

The current real adapter implementation is
`TwilioOutboundMessageAdapter.send_message(...)` in
`src/duna_orders/integrations/twilio_outbound.py`.

The retry claim behavior lives in
`PostgresOutboundAcknowledgementStore.claim_order_acknowledgement_for_send(...)`.
For an existing row with status `failed`:

* if `retry_failed=False`, the claim is suppressed with
  `suppressed_failed_without_retry`;
* if `retry_failed=True`, the store calls `_try_claim_failed_retry(...)`;
* `_try_claim_failed_retry(...)` updates the same `outbound_messages` row from
  `failed` to `sending`;
* the same `outbound_message_id` is reused;
* `attempt_count` is incremented;
* previous error fields, provider message id, and sent timestamp are cleared;
* no new outbound row is created.

For non-failed rows, `retry_failed=True` does not bypass idempotency:

* `send_requested` and `sending` are suppressed as in-progress;
* `sent` is suppressed as already sent;
* `unknown` is suppressed as unknown;
* no adapter call happens for those suppressed states.

Existing service outcome categories are:

* `OutboundAcknowledgementOutcome.SENT` = `sent`
* `OutboundAcknowledgementOutcome.SUPPRESSED_DUPLICATE` = `suppressed_duplicate`
* `OutboundAcknowledgementOutcome.FAILED_RETRYABLE` = `failed_retryable`
* `OutboundAcknowledgementOutcome.MAY_HAVE_SENT_INVESTIGATE` =
  `may_have_sent_investigate`
* `OutboundAcknowledgementOutcome.BLOCKED_PRECONDITION` =
  `blocked_precondition`

Existing outbound row statuses are:

* `send_requested`
* `sending`
* `sent`
* `failed`
* `unknown`

## Failed Categorization Audit

The retry policy depends on `failed` meaning: no send left our system, or the
provider definitively rejected the send before dispatch.

Current provider result outcomes are:

* `success`
* `failed`
* `unknown`

The service maps provider result outcomes to row statuses as follows:

* provider `success` -> `mark_sent(...)` -> row status `sent`;
* provider `failed` -> `mark_failed(...)` -> row status `failed`;
* provider `unknown` -> `mark_unknown(...)` -> row status `unknown`.

Current Twilio adapter mapping:

* successful Twilio response with message SID -> provider `success`;
* timeout (`TimeoutError` or `requests.Timeout`) -> provider `unknown`;
* Twilio 5xx/server error -> provider `unknown`;
* generic unclear exception -> provider `unknown`;
* missing provider message id after response -> provider `unknown`;
* Twilio REST status `400`, `401`, `403`, or `404` -> provider `failed`;
* all other Twilio REST errors -> provider `unknown`.

Current failed-mapped cases are therefore Twilio definitive rejections:

* invalid number or malformed/rejected-before-dispatch 4xx cases;
* auth failure;
* forbidden/not-found provider rejection cases.

Current ambiguous cases map to `unknown`:

* timeout;
* 5xx;
* generic unclear exception;
* missing provider message id;
* any non-4xx Twilio REST error not explicitly treated as definitive.

Audit result: retry implementation is not currently blocked by categorization.
No timeout, 5xx, or generic unclear exception path currently reaches `failed`.
If future adapter work makes any ambiguous, timeout, 5xx, or may-have-sent
outcome reach `failed`, retry UI implementation must stop. The fix belongs in
provider categorization by moving that outcome to `unknown`; it does not belong
in retry UI.

## Retry Policy

### 1. Retry Button Visibility

Show `Retry acknowledgement` only for outbound rows with status `failed`.

Never show retry for:

* `sent`;
* `sending`;
* `send_requested`;
* `unknown`.

Rationale:

* `sent` already reached provider acceptance and must not be resent from the
  normal operator UI.
* `sending` and `send_requested` are in-progress states. They may already have
  produced a provider-side send or may still complete.
* `unknown` means the system cannot prove whether the message was accepted.
  Showing retry there can double-message the customer.
* `unknown`, `sending`, and `send_requested` are customer-harm gates. There is
  no exception and no override in this milestone.

### 2. Retry Route

Retry must route through:

```python
OutboundAcknowledgementService.send_order_confirmed_acknowledgement(
    ...,
    retry_failed=True,
)
```

Rules:

* retry must reuse the same idempotency row;
* retry must not create a new `outbound_messages` row;
* UI must never call the provider adapter directly;
* UI must never decide send safety by itself;
* backend claim/idempotency remains final authority.

Rationale:

The UI is a display and intent surface. It can offer an action, but the store
claim is the only send authority. This protects against stale UI state,
double-clicks, multiple browser sessions, and concurrent operators.

### 3. Explicit Operator Confirmation Step

Before retry fires, require explicit confirmation with this exact text:

```text
Send this acknowledgement again? The previous attempt failed.
```

Rationale:

* retry is a real customer send;
* one extra click prevents misclicks;
* the wording reinforces that retry can send a WhatsApp message;
* this matches the approved-to-confirmed mental model, where status-only approve
  did not commit inventory until an explicit confirmation step.

### 4. Retry Is a Fresh Send for Categorization

Retry must be categorized exactly like any fresh outbound attempt.

Hard rules:

* if a retry attempt succeeds, the row becomes `sent`;
* if a retry attempt definitively fails before dispatch, the row remains or
  becomes `failed` and may be retryable again unless a future retry-limit policy
  says otherwise;
* if a retry attempt returns ambiguous, timeout, 5xx, unclear exception, or any
  may-have-sent result, the row becomes `unknown`;
* `unknown` after retry is non-retryable;
* the retry button must disappear for `unknown`;
* a retry that goes ambiguous must not be retried again.

Rationale:

This rule prevents retry UI from reintroducing double-send risk. A second
attempt that becomes ambiguous is no longer safely retryable from the UI.

### 5. Re-read After Retry Action

After retry action, UI must re-query and re-render current outbound state.

Rules:

* no stale `failed` or retryable display may remain after the action;
* the post-action display must come from current store state or an immediate
  rerun that re-queries current store state;
* if the backend suppresses the retry because the row changed, the UI must show
  the current safe state, not the stale pre-click state.

Rationale:

Retry changes the same row. A stale UI could keep offering retry after the row
has moved to `sending`, `sent`, or `unknown`.

### 6. Attempt Count and Last Failure Time

Hide `attempt_count` for now.

Hide last failure time for now.

Rationale:

`attempt_count` is currently an internal mechanic used by idempotency and audit
behavior. Failure-time display can be a later polish slice. These are deferred,
not forbidden forever.

## Operator Wording Invariant

Nothing in outbound UI may imply delivery or receipt.

There are no delivery/read callbacks. `sent` means handed to provider/Twilio and
accepted by that provider. It does not mean the customer saw the message.

Forbidden wording in current and future outbound UI:

* delivered;
* received;
* customer saw it;
* customer was notified;
* confirmed received.

Required future operator strings:

* failed message:

  ```text
  Acknowledgement was not sent. You can retry.
  ```

* retry button label:

  ```text
  Retry acknowledgement
  ```

* confirmation text:

  ```text
  Send this acknowledgement again? The previous attempt failed.
  ```

Post-retry success may say `sent`, but must never say delivered or received.

User-facing retry strings must not expose:

* `provider_message_id`;
* provider error codes;
* provider error messages;
* Twilio/provider internals.

## Future Implementation Tests

Do not write these tests in M8.6.3A. They are required for the future
implementation slice.

### Button Visibility

* retry button shows only for `failed`;
* named negative test for `sent`;
* named negative test for `sending`;
* named negative test for `send_requested`;
* named negative test for `unknown`.

### Retry Routing

* retry routes through service with `retry_failed=True`;
* retry uses the claim path;
* retry creates no new row;
* UI does not call the adapter directly;
* UI introduces no trusted-send path.

### Stale-State and Backend Defense

* backend refuses retry of a non-failed row even if UI showed a stale retry
  button;
* duplicate/idempotency safety remains final authority.

### Retry Goes Unknown

* ambiguous retry result moves row to `unknown`;
* retry button disappears;
* no retry of an ambiguous retry.

### Confirmation Step

* retry does not fire without explicit operator confirmation;
* confirmation wording is exact and provider-neutral.

### Re-read After Retry

* after retry, UI re-queries current outbound state;
* stale failed/retryable display does not persist.

### Provider Secrecy and Wording Invariant

* no retry string exposes `provider_message_id`;
* no retry string exposes error codes;
* no retry string exposes Twilio/provider internals;
* no retry string implies delivery, receipt, or customer saw it.

### Failed Categorization Audit

* tests or assertions confirm ambiguous, timeout, and 5xx outcomes cannot map to
  `failed`;
* failed-mapped provider outcomes are provably did-not-send cases.

## Non-goals

M8.6.3A explicitly does not include:

* implementation;
* send-behavior changes;
* callback, delivery, or read tracking;
* queue/worker;
* auto-send;
* payment-dependent content;
* parser behavior changes;
* `PROMPT_VERSION` changes;
* `StorageInterface` extension;
* `OrderService` coupling;
* provider adapter changes;
* retry-limit or max-attempts policy;
* `attempt_count` display;
* failure-time display.

## Deferrals

Deferred future work:

* `attempt_count` display;
* last failure time display;
* retry-limit/max-attempts policy;
* callback/delivery/read status;
* queue/worker;
* provider-specific admin diagnostics outside user-facing UI.

## Implementation Gate

Retry UI implementation must not begin unless the failed categorization audit
confirms that every failed outcome is provably did-not-send and every ambiguous
outcome maps to `unknown`.

The current implementation passes that gate: timeout, 5xx, generic unclear
exceptions, missing provider message id, and other ambiguous outcomes map to
`unknown`; current failed mappings are limited to explicit provider 4xx
rejections treated as definitive did-not-send cases.
