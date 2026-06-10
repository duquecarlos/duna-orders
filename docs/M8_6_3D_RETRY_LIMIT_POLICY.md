# M8.6.3D Retry Limit Policy

Status: design policy only.

Baseline: `d5bbab3 docs(outbound): record retry execution smoke`

M8.6.3D does not implement retry limits. It defines the smallest safe production
policy for limiting operator retry attempts after a failed outbound
acknowledgement. The policy builds on M8.6.3A/B/C:

* M8.6.3A defined retry safety rules.
* M8.6.3B implemented guarded retry UI.
* M8.6.3C proved one real retry execution smoke: the same outbound row was
  reused, row count stayed `1`, `attempt_count` increased from `1` to `2`,
  status became `sent`, and the WhatsApp message was received by a safe test
  recipient.

## Pre-flight Contract Findings

### Current Retry Behavior

Retry UI is rendered in `pages/2_Orders_Today.py` inside
`_render_outbound_acknowledgement_action(...)`.

The page reads the current outbound acknowledgement row through:

```python
setup.acknowledgement_store.get_for_order_acknowledgement(...)
```

It maps that row through:

```python
map_acknowledgement_status_to_ui_state(...)
```

Current failed-row UI mapping lives in
`src/duna_orders/ui/outbound_acknowledgement.py`:

* `status == "failed"` renders
  `Acknowledgement was not sent. You can retry.`;
* `show_send_button=False`;
* `show_retry_button=True`.

Orders Today renders `Retry acknowledgement` only when
`status_state.show_retry_button` is true. The first click only sets a
per-order confirmation flag in Streamlit session state and reruns. It does not
call the service.

Confirmed retry calls:

```python
setup.service.send_order_confirmed_acknowledgement(
    ...,
    retry_failed=True,
)
```

The UI does not call the provider adapter directly and does not create outbound
rows.

The service method is:

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

The service still performs all normal precondition checks before retry:

* tenant id required;
* order id required;
* sender phone number required;
* requested-by/operator identity required;
* tenant-scoped order read;
* order must exist for that tenant;
* order must be `confirmed`;
* customer phone must be present.

After preconditions, the service calls:

```python
self._store.claim_order_acknowledgement_for_send(..., retry_failed=retry_failed)
```

`PostgresOutboundAcknowledgementStore.claim_order_acknowledgement_for_send(...)`
handles existing failed rows as follows:

* if `existing.status == "failed"` and `retry_failed=False`, the claim is
  suppressed with `suppressed_failed_without_retry`;
* if `existing.status == "failed"` and `retry_failed=True`, it calls
  `_try_claim_failed_retry(...)`;
* `_try_claim_failed_retry(...)` updates the same row from `failed` to
  `sending`;
* it increments `attempt_count` with
  `attempt_count=OutboundMessageRow.attempt_count + 1`;
* it clears `last_error_code`, `last_error_message`, `provider_message_id`, and
  `sent_at`;
* it does not create a new outbound row.

Current behavior has no max-attempt check.

If a retry definitively fails again, the service maps provider `failed` to
`mark_failed(...)`, which sets the same row back to `status="failed"`. Because
there is no max-attempt check today, that row can become retryable again in the
UI.

If a retry goes ambiguous or unknown, the service maps provider `unknown` to
`mark_unknown(...)`, which sets the row to `status="unknown"`. Current UI hides
retry for `unknown`, and the store suppresses future claims for `unknown` even
when `retry_failed=True`.

### Existing Design Docs

M8.6.3A says retry-limit/max-attempts policy is deferred. It also states:

* retry is allowed only for `failed`;
* `unknown`, `sending`, and `send_requested` are customer-harm gates;
* retry routes through the service with `retry_failed=True`;
* retry reuses the same outbound row;
* retry does not create a new row;
* attempt count and last failure time are hidden for now;
* a retry that becomes unknown is non-retryable;
* if a retry definitively fails, it may be retryable again unless a future
  retry-limit policy says otherwise.

M8.6.3B docs record the implemented guarded retry UI:

* failed rows show `Acknowledgement was not sent. You can retry.`;
* failed rows show `Retry acknowledgement`;
* first click opens explicit confirmation only;
* confirmed retry calls the existing service path with `retry_failed=True`;
* no provider adapter calls or outbound row creation happen in UI;
* retry-limit/max-attempts policy, `attempt_count` display, and failure-time
  display remain deferred.

M8.6.3C docs record the real retry execution smoke:

* a safe operator-controlled recipient was used;
* the retry reused `out_ui_retry_execution_smoke_20260610`;
* outbound row count stayed `1`;
* `attempt_count` increased from `1` to `2`;
* final status was `sent`;
* provider message id and sent timestamp were populated;
* the WhatsApp message was received.

### Current UI

Current UI hides `attempt_count`.

Current UI hides last failure time.

Current UI hides provider error codes and provider error messages.

Current UI hides provider internals such as provider message id and Twilio
details in operator-facing acknowledgement status and retry strings.

## Current Risk

Current retry behavior is conservative for ambiguous states, but still has one
open production-policy gap: a row can become `failed` again after an operator
retry definitively fails before dispatch. Because the UI shows retry for every
failed row today, that row can be offered for another retry.

This is safer than retrying `unknown`, but it is still not a production policy:

* repeated clicks may not fix invalid number, malformed request, auth, or other
  definitive rejection categories;
* repeated retries increase operator confusion;
* repeated retries increase customer-harm risk if future categorization changes
  accidentally move an ambiguous outcome into `failed`;
* UI-only hiding is insufficient because browser state can be stale.

## Recommended Policy

Allow at most two total attempts per outbound acknowledgement row.

Definitions:

* attempt 1 is the original send attempt;
* attempt 2 is one explicit operator retry.

Rules:

* If `status == "failed"` and `attempt_count < 2`, show
  `Retry acknowledgement`.
* If `status == "failed"` and `attempt_count >= 2`, hide
  `Retry acknowledgement`.
* If `status == "failed"` and `attempt_count >= 2`, show:

  ```text
  Acknowledgement was not sent. Manual follow-up is required.
  ```

* Do not expose `attempt_count` in the operator UI yet.
* Do not expose provider error codes or provider error messages.
* Do not expose last failure time yet.
* Keep `unknown`, `sending`, and `send_requested` as non-retryable
  customer-harm gates.
* Keep `sent` non-retryable.
* Keep no-record behavior unchanged: no outbound row can still show
  `Send acknowledgement` when all other send gates pass.
* Backend must enforce the max-attempt rule. UI visibility may reflect current
  row state, but UI must not be the final send authority.

Rationale:

* One retry is enough to recover from transient definitive failed-before-dispatch
  conditions.
* Invalid, malformed, and auth failures are usually not fixed by repeated
  button clicks.
* Repeated retries create unnecessary operator ambiguity.
* Repeated retries increase customer-harm risk if provider categorization
  regresses later.
* `unknown` still means may-have-sent and must never be retried.
* A max-attempt policy must be enforced server-side because UI can be stale.

## Operator Wording

Allowed strings:

```text
Acknowledgement was not sent. You can retry.
Retry acknowledgement
Send this acknowledgement again? The previous attempt failed.
Acknowledgement was not sent. Manual follow-up is required.
```

Wording invariant:

* do not say delivered;
* do not say received;
* do not say notified;
* do not say customer saw it;
* do not say confirmed received.

`sent` means handed to the provider and accepted by the provider. It does not
mean delivered, read, or seen by the customer.

## Backend Enforcement Requirement

M8.6.3E must add backend enforcement before or in the same slice as UI changes.

Required backend behavior:

* `claim_order_acknowledgement_for_send(..., retry_failed=True)` must refuse to
  claim failed rows when `attempt_count >= 2`;
* refusal must not call the provider adapter;
* refusal must not create a new row;
* refusal must leave the existing row intact;
* refusal must return a provider-neutral suppression reason or service outcome
  suitable for UI mapping;
* stale UI showing a retry button must not bypass the max-attempt rule.

The UI may hide retry based on `status == "failed"` and `attempt_count >= 2`,
but backend/store/service enforcement remains the final authority.

## Future Implementation Tests

Required tests for the implementation slice:

* failed `attempt_count=1` shows `Retry acknowledgement`;
* failed `attempt_count=2` hides `Retry acknowledgement`;
* failed `attempt_count=2` shows
  `Acknowledgement was not sent. Manual follow-up is required.`;
* backend refuses `retry_failed=True` when `attempt_count >= 2`;
* stale UI cannot bypass the max-attempt policy;
* max-attempt refusal does not call the provider adapter;
* max-attempt refusal does not create a new outbound row;
* retry that definitively fails and increments to `attempt_count=2` becomes the
  manual-follow-up state;
* retry that goes `unknown` remains `unknown` and hides retry;
* `sent`, `sending`, `send_requested`, and `unknown` still hide retry;
* no provider details appear in max-attempt strings;
* no delivered, received, notified, customer-saw, or confirmed-received wording
  appears.

## Non-goals

M8.6.3D explicitly does not include:

* implementation;
* send behavior changes;
* callback, delivery, or read tracking;
* queue/worker;
* auto-send;
* payment-dependent content;
* parser behavior changes;
* `PROMPT_VERSION` changes;
* `StorageInterface` extension;
* `OrderService` coupling;
* provider adapter changes;
* `attempt_count` display;
* failure-time display;
* admin diagnostics UI;
* provider-specific retry recommendations.

## Deferrals

Deferred future work:

* implementation of backend max-attempt enforcement;
* UI mapping for failed max-attempt rows;
* optional future `attempt_count` display;
* optional future last failure time display;
* admin diagnostics outside the operator-facing acknowledgement UI;
* delivery/read callbacks;
* queue/worker behavior.

## Implementation Gate

M8.6.3E must not ship a retry-limit UI-only change.

Implementation is blocked unless backend enforcement is included first or in the
same slice. Tests must prove stale UI cannot bypass the max-attempt rule and
that retry remains unavailable for `unknown`, `sending`, `send_requested`, and
`sent`.

Provider details must remain hidden, and no outbound UI wording may imply
delivery, receipt, customer notification, customer visibility, or confirmed
receipt.
