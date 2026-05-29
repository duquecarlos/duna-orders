# M8 Architecture — WhatsApp Conversational Ordering

## 1. Purpose & objective

M8 adds WhatsApp conversational ordering to Duna Orders.

A customer texts the restaurant through WhatsApp. The system receives the message, resolves the tenant, maintains a conversation session, uses an LLM to produce structured draft updates, and lets the operator confirm the order before any final commitment message is sent.

M8 also introduces the storage foundation required for conversational ordering: Postgres. Google Sheets was acceptable for early operator workflows and demo visibility, but conversational ordering requires transactional behavior, idempotency, queueing, session versioning, outbox semantics, status callbacks, and safe operator confirmation. Those responsibilities should not live on Google Sheets.

Existing demo data is re-seeded fresh into Postgres using deterministic seeders. It is not migrated row-by-row from Google Sheets.

M8 keeps the core product commitment:

> Channels are replaceable; the order engine is the product.

WhatsApp is the first conversational channel. The architecture must keep future Meta direct WhatsApp, Telegram, web chat, and other channels viable.

## 2. Guiding principles

### 2.1 Safety by construction, not by discipline

Outbound customer messaging is impossible by default. The system must require explicit configuration changes and policy approval before any real message can leave the platform.

Accidental real customer messaging should require multiple protections to be deliberately disabled.

### 2.2 Replaceability everywhere

Replaceability applies to:

* channels: Twilio WhatsApp Sandbox → Meta direct WhatsApp → Telegram → future channels;
* models: Anthropic → OpenAI → Gemini;
* storage: Postgres now, other production storage later if required;
* UI: Streamlit now, future web app later.

Core business logic remains in services, not in provider adapters or UI pages.

### 2.3 Observability from day one

Every relevant event must be observable:

* inbound messages;
* session transitions;
* conversation events;
* LLM calls;
* token usage and cost;
* outbound decisions;
* suppressed outbound messages;
* provider delivery statuses;
* operator actions.

The product must support cost-per-completed-order analysis from the first pilot.

### 2.4 Conversational unification

“All-in-one” customers and step-by-step customers use the same session logic.

An all-in-one customer may reach operator review after one turn. A step-by-step customer may require multiple clarification turns. Both flows use the same session, draft, LLM output, and operator confirmation model.

### 2.5 Two outbound categories, different gates

The bot may autonomously send safe clarification messages, subject to the safety harness.

The bot cannot send commitment messages without operator confirmation.

The LLM may propose a next action, but the policy engine independently validates whether that action is allowed.

## 3. Runtime topology

M8 introduces two runtime processes sharing the same Python packages and the same Postgres database.

### 3.1 Process A — FastAPI webhook service

Responsibilities:

* receive inbound WhatsApp webhooks;
* verify Twilio signatures;
* resolve tenant-channel bindings;
* persist inbound messages;
* enqueue conversation jobs;
* expose outbound status callback endpoint;
* run in-process background workers for conversation jobs;
* run in-process outbound dispatcher loop;
* expose health endpoint for deployment.

Endpoints:

* `POST /webhook/whatsapp`
* `POST /webhook/twilio/status`
* `GET /health`

The webhook endpoint must acknowledge Twilio quickly. It does not run the full LLM conversation turn synchronously.

Target inbound path:

1. verify signature;
2. resolve tenant binding;
3. persist `InboundMessage`;
4. enqueue `Job(kind=conversation_turn)`;
5. return HTTP 200.

Conversation processing happens after acknowledgement.

### 3.2 Process B — Streamlit operator UI

The existing Streamlit UI is extended into a polling operator control panel.

It is not treated as a real-time chat surface.

Responsibilities:

* show active sessions;
* show current draft;
* show conversation history;
* show LLM operator summary;
* show suppressed outbound messages;
* show failed outbound messages;
* allow operator identity selection;
* allow operator confirmation;
* prevent stale confirmation.

The Streamlit UI must check the current session version before confirming an order. If the operator’s local view is stale, confirmation is rejected and the page must refresh.

### 3.3 Deployment assumption for M8

M8 assumes a single FastAPI service instance for in-process worker partitioning correctness.

Horizontal multi-instance scaling is deferred. If multiple FastAPI instances are introduced later, session partitioning and job ownership must be moved fully into Postgres-level coordination or an external queue.

## 4. Storage stack

M8 moves runtime storage to Postgres.

### 4.1 Database

* Postgres hosted on Neon for pilot.
* Neon dev branch or local Docker Postgres for local development.
* Railway hosts the FastAPI service and Streamlit service.
* Both services share the same Neon Postgres database.

### 4.2 ORM and migrations

* SQLAlchemy 2.0.
* Alembic for migrations.
* SQLAlchemy sync mode for M8.
* SQLAlchemy built-in connection pooling.

### 4.3 Storage abstraction

Existing `StorageInterface` remains the persistence boundary.

Implementations:

* `InMemoryStorage`: retained for unit tests.
* `GoogleSheetsStorage`: legacy/pilot history, not the M8 runtime target.
* `PostgresStorage`: new runtime implementation.

The name is `PostgresStorage`. Do not introduce transitional names such as `GooglePostgresStorage`.

### 4.4 Demo data

Existing deterministic demo seeders are reused to seed Postgres fresh.

The demo tenant remains:

* tenant id: `el-fogon-colombiano`;
* display name: El Fogón Colombiano;
* customers: 730;
* products: 52;
* orders: 1,500;
* order_items: 3,889;
* seed: 42.

Google Sheets demo data is not migrated row-by-row.

## 5. Data model

All M8 entities live in Postgres.

Append-only tables keep historical event records.

Snapshot tables keep current state and use monotonic versions where concurrent updates are possible.

### 5.1 TenantChannelBinding

Purpose: map provider/channel inbound messages to a Duna tenant.

Fields:

* `id`
* `tenant_id`
* `provider`
* `provider_account_sid`
* `messaging_service_sid`
* `channel_address`
* `environment`
* `is_active`
* `created_at`

Resolution key:

* `provider_account_sid`
* `to_address`
* `environment`

For M8 sandbox, the Twilio sandbox binding maps explicitly to `el-fogon-colombiano`.

This sandbox binding does not prove production multi-tenant routing. Production routing is post-M8.

### 5.2 InboundMessage

Append-only record of provider inbound messages.

Fields:

* `id`
* `tenant_id`
* `provider_event_id`
* `provider_retry_id`
* `account_sid`
* `messaging_service_sid`
* `channel`
* `from_address`
* `to_address`
* `body`
* `payload_hash`
* `received_at`
* `received_attempt_number`
* `session_id`
* `raw_payload`

Rules:

* `provider_event_id` is unique.
* For Twilio, `provider_event_id` is the Twilio `MessageSid`.
* Duplicate provider events do not create duplicate processing jobs.
* `raw_payload` is environment-gated.

### 5.3 Session

Mutable snapshot of the current conversation state.

Fields:

* `id`
* `tenant_id`
* `customer_phone`
* `channel`
* `opened_at`
* `last_activity_at`
* `status`
* `current_draft`
* `version`
* `linked_order_id`
* `current_model_id`
* `current_prompt_version`
* `current_catalog_snapshot_id`
* `total_cost_usd`

Status values:

* `open`
* `awaiting_operator`
* `confirmed`
* `cancelled`
* `expired`
* `failed`

`processing` is intentionally not a persisted session status in M8. Processing state belongs to the `Job` table. Session status represents business state, not worker execution state.

### 5.4 ConversationEvent

Append-only event stream for a session.

Fields:

* `id`
* `tenant_id`
* `session_id`
* `sequence_number`
* `source`
* `event_type`
* `body_or_payload`
* `created_at`

Sources:

* `customer`
* `bot`
* `operator`
* `system`
* `llm`

The session does not store the full conversation history as one growing JSON blob. Conversation history is reconstructed from events.

### 5.5 OutboundMessage

Mutable header for outbound communication.

Fields:

* `id`
* `idempotency_key`
* `tenant_id`
* `session_id`
* `intent`
* `body`
* `status`
* `suppressed_reason`
* `operator_confirmed_by`
* `session_version_at_dispatch`
* `provider_message_sid`
* `created_at`
* `sent_at`
* `retry_count`
* `last_retry_at`

Rules:

* `idempotency_key` is unique.
* Commitment bodies are deterministic.
* Commitment bodies never come directly from raw LLM output.

Statuses:

* `queued`
* `suppressed`
* `sending`
* `sent`
* `delivered`
* `read`
* `failed`
* `undelivered`

### 5.6 OutboundStatusEvent

Append-only provider status history.

Fields:

* `id`
* `outbound_message_id`
* `provider_message_sid`
* `provider_status`
* `raw_payload`
* `received_at`
* `provider_sequence`

Status callbacks may be duplicated or out of order. Derived latest status must use safe-monotone rules and never regress from stronger delivery states to weaker states.

### 5.7 LLMCallLog

Append-only record for every LLM call.

Fields:

* `id`
* `session_id`
* `turn_number`
* `model_id`
* `prompt_version`
* `catalog_snapshot_id`
* `catalog_hash`
* `input_tokens`
* `output_tokens`
* `cached_tokens`
* `latency_ms`
* `cost_usd`
* `outcome`
* `schema_validation_status`
* `called_at`

Outcomes:

* `parsed_ok`
* `schema_invalid_retry`
* `schema_invalid_final`
* `low_confidence`
* `timeout`
* `rate_limited`
* `failed`

### 5.8 OperatorAction

Append-only audit record for operator activity.

Fields:

* `id`
* `session_id`
* `operator_identity`
* `action`
* `session_version_at_action`
* `payload`
* `created_at`

Actions:

* `claim`
* `release`
* `confirm`
* `override`
* `reject`
* `reopen`

### 5.9 CatalogSnapshot

Versioned catalog context for LLM prompts.

Fields:

* `id`
* `tenant_id`
* `product_count`
* `catalog_hash`
* `prompt_cache_key`
* `generated_at`
* `is_active`

Every LLM call records the catalog snapshot used.

### 5.10 Job

Postgres-as-queue table.

Fields:

* `id`
* `kind`
* `payload`
* `partition_key`
* `status`
* `locked_by`
* `locked_at`
* `attempts`
* `last_error`
* `created_at`
* `available_at`

Kinds:

* `conversation_turn`
* `outbound_dispatch`

Statuses:

* `queued`
* `locked`
* `done`
* `failed`

## 6. Concurrency strategy

M8 uses layered concurrency protection.

### 6.1 Layer 1 — In-process partitioning

Conversation jobs use a deterministic `partition_key`.

For conversational turns, the partition key is derived from:

* `tenant_id`
* `channel`
* `customer_phone`

Jobs for the same customer session are routed to the same in-process worker.

M8 assumes one FastAPI service instance. Multi-instance worker partitioning is deferred.

### 6.2 Layer 2 — Postgres job claiming

Workers claim jobs using Postgres row locking.

Pattern:

* claim queued available jobs;
* use `SELECT ... FOR UPDATE SKIP LOCKED`;
* set job to `locked`;
* set `locked_by`;
* set `locked_at`;
* increment attempts.

If a worker crashes, lock expiry makes the job available again.

### 6.3 Layer 3 — Optimistic session versioning

Session updates require expected-version matching.

Flow:

1. worker reads session version `N`;
2. worker performs conversation processing;
3. worker writes only if current version is still `N`;
4. successful write bumps version to `N+1`;
5. conflict causes reload and retry once;
6. second conflict marks session as failed for operator review.

### 6.4 Layer 4 — ACID transactions

Business-critical transitions happen inside one Postgres transaction.

Atomic confirmation includes:

* order creation;
* session status advance;
* session order link;
* outbound commitment row creation;
* operator action log.

All-or-nothing.

## 7. Tenant-channel resolution

Inbound messages do not trust payload-level tenant identifiers.

Tenant resolution uses `TenantChannelBinding`.

Lookup:

* provider account SID;
* inbound `to_address`;
* environment.

No match behavior:

* return 200 to Twilio;
* do not enqueue conversation processing;
* log unmatched inbound for operations review.

M8 sandbox behavior:

* Twilio Sandbox maps to `el-fogon-colombiano`;
* this is a deliberate M8 limitation;
* production tenant-channel binding is post-M8.

## 8. Webhook ACK, processing separation, and Postgres-as-queue

### 8.1 Webhook endpoint

The inbound webhook performs only fast, safe work:

1. verify Twilio signature;
2. resolve tenant binding;
3. persist inbound message;
4. deduplicate by provider event id;
5. enqueue conversation job;
6. return 200.

Target acknowledgement time: under 100 ms.

### 8.2 Conversation worker

The conversation worker runs outside the webhook request path.

Flow:

1. claim `conversation_turn` job;
2. resolve or create session;
3. append customer `ConversationEvent`;
4. run LLM turn once M8.5 introduces the LLM layer;
5. update session snapshot;
6. enqueue outbound proposal if needed;
7. mark job done.

### 8.3 Outbound dispatcher

The outbound dispatcher runs as a background loop.

Flow:

1. poll `outbound_dispatch` jobs;
2. claim job;
3. load outbound message;
4. evaluate policy;
5. if suppressed, store reason and finish;
6. if allowed, send through channel adapter;
7. update provider message SID and status;
8. rely on status callback for delivery updates.

## 9. Outbox, policy engine, channel dispatcher, and status callback

M8 separates outbound responsibilities.

### 9.1 OutboxService

Only entry point for creating outbound rows.

Responsibilities:

* create `OutboundMessage`;
* assign idempotency key;
* enqueue `outbound_dispatch` job.

It does not send messages.

### 9.2 OutboundPolicyEngine

Pure policy evaluator.

Input:

* outbound message;
* session context;
* tenant-channel context;
* config;
* opt-out/rate-limit context.

Output:

* allowed or suppressed;
* suppression reason;
* evaluated guards.

It does not call Twilio.

### 9.3 ChannelDispatcher

Background dispatcher.

Responsibilities:

* claim outbound jobs;
* evaluate policy;
* send approved messages through `ChannelAdapter`;
* update outbound message status;
* schedule retries for transient failures.

### 9.4 StatusCallbackHandler

Receives provider delivery callbacks.

Responsibilities:

* verify provider signature;
* append `OutboundStatusEvent`;
* update derived outbound status using non-regressing rules.

### 9.5 ChannelAdapter

Provider adapter interface.

Responsibilities:

* parse provider inbound payloads;
* verify provider signatures;
* send outbound messages.

M8 implementation:

* Twilio WhatsApp Sandbox.

Future implementations:

* Meta direct WhatsApp;
* Telegram;
* other channels.

## 10. OutboundIntent taxonomy and autonomy matrix

### 10.1 OutboundIntent values

* `CLARIFY_MISSING_INFO`
* `CLARIFY_SUBSTITUTION`
* `ACKNOWLEDGE_RECEIPT`
* `OPERATOR_REVIEW_NOTICE`
* `COMMITMENT_CONFIRMATION`
* `FAILURE_OR_HANDOFF`
* `PAYMENT_REQUEST`

`PAYMENT_REQUEST` is deferred and not active in M8.

### 10.2 Autonomy policy

| Intent                    | Autonomous? | Operator required? | Notes                                                |
| ------------------------- | ----------: | -----------------: | ---------------------------------------------------- |
| `CLARIFY_MISSING_INFO`    |         yes |                 no | Allowed only inside safety harness.                  |
| `CLARIFY_SUBSTITUTION`    | conditional |          sometimes | Autonomous only when substitution is catalog-backed. |
| `ACKNOWLEDGE_RECEIPT`     |         yes |                 no | Must not imply order commitment.                     |
| `OPERATOR_REVIEW_NOTICE`  |         yes |                 no | Tells customer the order is under review.            |
| `COMMITMENT_CONFIRMATION` |       never |                yes | Deterministic message after operator confirmation.   |
| `FAILURE_OR_HANDOFF`      |         yes |                 no | Used for safe handoff/issue messages.                |
| `PAYMENT_REQUEST`         |          no |           deferred | Not implemented in M8.                               |

The LLM may propose an intent. The policy engine validates whether that intent is allowed.

## 11. Safety guards

The `OutboundPolicyEngine` evaluates guards in order.

First failure suppresses the outbound. All evaluated guards are recorded.

1. `tenant_channel_enabled`
2. `environment_binding_matches`
3. `kill_switch`
4. `mode_check`
5. `allowlist`
6. `session_status_allows_outbound`
7. `idempotency`
8. `whatsapp_window_or_template`
9. `rate_limit`
10. `length_and_basic_content`
11. `commitment_requires_operator`
12. `opt_out_list`

### 11.1 Guard behavior

`tenant_channel_enabled` checks that the tenant-channel binding is active.

`environment_binding_matches` prevents sandbox credentials from being used as production bindings or production credentials from being used in sandbox context.

`kill_switch` blocks all outbound unless `OUTBOUND_ENABLED` is explicitly enabled.

`mode_check` blocks real sends unless outbound mode allows live dispatch.

`allowlist` blocks non-production outbound to numbers not in the configured allowlist.

`session_status_allows_outbound` blocks outbound for cancelled, expired, failed, or otherwise closed sessions.

`idempotency` blocks duplicate outbound sends.

`whatsapp_window_or_template` enforces production WhatsApp window/template policy. In M8 sandbox it logs a warning and passes.

`rate_limit` applies per-tenant and per-customer limits.

`length_and_basic_content` enforces message length and blocks unsafe commitment text.

`commitment_requires_operator` requires valid operator identity and exact session version match for commitment messages.

`opt_out_list` blocks outbound to opted-out customer numbers.

## 12. LLM structured-output design

### 12.1 Interface

M8 introduces `StructuredTurnClient`.

Method concept:

`generate_turn(model_id, prompt_parts, schema, cache_policy) -> StructuredLLMResult[TurnOutput]`

The interface is capability-aware. M8 only needs structured turn generation.

Provider adapters normalize:

* parsed output;
* raw response id;
* model id;
* input tokens;
* output tokens;
* cached tokens;
* latency;
* cost;
* schema validation status;
* provider metadata.

### 12.2 Initial provider

Initial M8 model:

* Anthropic Claude Haiku 4.5.

OpenAI and Gemini adapters are deferred to the multi-model/eval slice.

### 12.3 Structured output rules

Provider-native structured output is required.

Pydantic validates after provider output.

Raw freeform JSON parsing is not the normal path.

### 12.4 TurnOutputSchema

Minimum fields:

* `action`
* `updated_draft_patch`
* `draft_completeness`
* `catalog_resolution`
* `next_question`
* `operator_summary`
* `confidence`
* `safety_flags`

Actions:

* `ASK`
* `READY_FOR_OPERATOR`
* `NEEDS_OPERATOR_REVIEW`
* `ERROR`

`updated_draft_patch` uses additive or replacement operations. The LLM does not overwrite the full draft blindly.

`draft_completeness` tracks:

* customer name;
* customer phone;
* fulfillment type;
* address;
* items;
* notes;
* payment method.

Each field status:

* `known`
* `missing`
* `ambiguous`

`catalog_resolution` tracks:

* matched items;
* ambiguous items;
* unavailable items;
* unknown items.

`next_question` is required when `action == ASK`.

`operator_summary` is always populated.

`confidence` is between 0.0 and 1.0.

`safety_flags` include risks such as:

* price promise;
* allergen claim;
* payment request;
* unsupported substitution;
* unclear catalog item.

### 12.5 Commitment rule

The LLM never creates the final commitment message.

Commitment messages are deterministically rendered from the confirmed structured order.

### 12.6 Malformed-output and low-confidence policy

If schema validation fails on the first attempt:

* retry once with validation feedback.

If schema validation fails again:

* mark result as `NEEDS_OPERATOR_REVIEW`;
* do not send outbound.

If confidence is below 0.6:

* cannot emit `READY_FOR_OPERATOR`;
* may ask a safe single clarification question;
* otherwise escalates to operator review.

If safety flags are non-empty:

* escalate to operator review.

Provider timeout:

* mark session failed or processing failed for operator attention;
* do not send an automatic apology in M8.

Provider rate limit:

* retry with backoff;
* if still unresolved, operator review.

## 13. Catalog versioning

Every LLM turn uses a `CatalogContext`.

Fields:

* `tenant_id`
* `catalog_snapshot_id`
* `product_count`
* `generated_at`
* `catalog_hash`
* `prompt_cache_key`

Every `LLMCallLog` stores:

* `catalog_snapshot_id`;
* `catalog_hash`;
* `prompt_version`;
* `model_id`.

This makes parser behavior debuggable. If the bot offers a stale item, the exact catalog snapshot can be identified.

## 14. Operator identity and atomic confirmation flow

### 14.1 Operator identity

M8 uses a lightweight operator identity pool.

Configuration:

* comma-separated `OPERATORS` value;
* Streamlit dropdown;
* selected identity stored in Streamlit session state.

This is not real authentication.

It is an honor-system identity mechanism acceptable only for a small known-operator pilot.

Real authentication is deferred until the operator UI moves beyond Streamlit or requires external users.

### 14.2 Atomic confirmation flow

Flow:

1. operator views session at version `N`;
2. operator clicks Confirm;
3. server verifies session is still `awaiting_operator`;
4. server verifies current version is still `N`;
5. if stale, confirmation is rejected;
6. inside one transaction:

   * create order from current draft;
   * link session to order;
   * set session status to `confirmed`;
   * bump session version;
   * create deterministic commitment outbound row;
   * append operator action;
7. outbound dispatcher handles commitment send asynchronously.

### 14.3 Failure cases

If order is created but commitment send fails:

* order remains confirmed;
* outbound is marked failed or undelivered;
* operator UI surfaces retry.

Retry creates a new outbound message with a new idempotency key.

If customer sends a new message before confirmation:

* session version changes;
* operator confirmation is rejected as stale.

If customer sends a new message after confirmation:

* M8 opens an amendment session requiring operator review;
* autonomous amendment handling is deferred.

## 15. PII handling

### 15.1 Principles

M8 stores enough information to operate and debug the pilot, but does not turn logs into an uncontrolled private-data warehouse.

### 15.2 Storage policy

Customer phones:

* normalized to E.164;
* stored long-term;
* masked by default in UI.

Raw inbound payloads:

* stored in dev/staging;
* redacted in production/runtime;
* default raw retention: 30 days.

Conversation text:

* stored for pilot analysis;
* default retention: 1 year;
* configurable later.

Prompt logs:

* full prompt text only in dev/staging;
* production/runtime stores hashes and structured variables.

Delivery addresses:

* stored as part of order data;
* masked in listings;
* visible in detail views for operators.

Operator actions:

* stored long-term as audit trail.

## 16. Deployment

### 16.1 Hosting

M8 pilot target:

* Railway project;
* FastAPI webhook service;
* Streamlit operator UI service.

### 16.2 Database

* Neon Postgres.
* Free tier acceptable for pilot.
* Branching used for dev/staging where practical.

### 16.3 Local development

* local FastAPI service;
* ngrok tunnel for Twilio Sandbox;
* local or Neon dev Postgres.

ngrok is for development only, not pilot runtime.

### 16.4 Secrets

Secrets are stored in deployment environment configuration, never in repo.

High-level required configuration includes:

* database connection;
* Twilio account credentials;
* Twilio WhatsApp sender;
* Anthropic API key;
* operators;
* outbound safety settings;
* environment;
* dashboard target.

No `.env` values are documented in architecture files.

## 17. Idle session expiry and cost circuit breaker

### 17.1 Idle session expiry

Default expiry:

* 30 minutes after `last_activity_at`.

A background sweeper expires idle sessions periodically.

Operators may reopen expired sessions through an operator action.

### 17.2 Cost circuit breaker

Each tenant has a daily LLM cost cap.

Default pilot soft cap:

* 2 USD per tenant per day.

At 80%:

* operator UI warning.

At 100%:

* autonomous outbound disabled;
* new LLM calls disabled or downgraded to operator-review-only mode;
* affected sessions are marked cost-paused or surfaced as requiring operator attention.

Purpose:

* prevent runaway LLM loops;
* catch prompt-injection or retry failures;
* keep pilot cost bounded.

This is not expected to control normal pilot cost, which should be low.

## 18. Required test matrix before first real send

Before any real Twilio Sandbox outbound send, the following must pass.

### 18.1 Inbound tests

* invalid Twilio signature rejected;
* unknown tenant/channel binding returns 200 but is not processed;
* duplicate Twilio `MessageSid` does not process twice;
* provider retry does not create duplicate session event;
* two messages for same session key are serialized or conflict safely.

### 18.2 LLM tests

* malformed structured output retries once;
* malformed output after retry becomes operator review;
* malformed output never sends outbound;
* low confidence never reaches ready-for-operator;
* catalog ambiguity escalates when no safe single question exists;
* provider timeout does not auto-message the customer.

### 18.3 Outbox and policy tests

* each safety guard suppresses independently;
* policy result records evaluated guards;
* outbound row exists before send attempt;
* idempotency key prevents duplicate dispatch.

### 18.4 Status callback tests

* duplicate callbacks are idempotent;
* out-of-order callbacks do not regress status;
* failed or undelivered status surfaces in operator UI.

### 18.5 Commitment tests

* missing operator identity blocks commitment;
* stale session version blocks confirmation;
* double-click confirmation is idempotent;
* failed send can be retried through new outbound row;
* post-confirm customer correction opens amendment session.

### 18.6 Transport tests

* mock channel adapter cannot reach Twilio;
* real Twilio adapter is used only in explicitly marked live sandbox tests.

## 19. M8 slicing plan

### M8.0 — Architecture lock

Deliverables:

* architecture document;
* decisions update;
* roadmap update.

No code.

### M8.1A — Postgres foundation

Scope:

* SQLAlchemy 2.0 foundation;
* Alembic foundation;
* `PostgresStorage` skeleton;
* core existing runtime tables;
* existing storage tests passing where applicable.

No webhook. No Twilio. No queue. No LLM. No outbound.

### M8.1B — Demo/runtime model parity

Scope:

* existing domain persistence mapped into Postgres;
* current runtime flows supported by `PostgresStorage`;
* existing service tests passing against Postgres-compatible behavior.

No webhook. No Twilio. No queue. No LLM. No outbound.

### M8.1C — Deterministic demo reseed and dashboard parity

Scope:

* re-seed deterministic demo data into Postgres;
* dashboard works from Postgres;
* demo reference-date behavior preserved;
* dashboard assumptions adjusted for Postgres.

No webhook. No Twilio. No LLM. No outbound.

### M8.1D — FastAPI inbound skeleton

Scope:

* FastAPI service skeleton;
* health endpoint;
* Twilio signature verification;
* `TenantChannelBinding`;
* `InboundMessage`;
* Twilio `MessageSid` idempotency;
* immediate ACK path.

No session lifecycle beyond inbound persistence. No LLM. No outbound.

### M8.2 — Job queue and session lifecycle

Scope:

* `Job` table;
* Postgres-as-queue claim pattern;
* worker scaffolding;
* session entity;
* conversation events;
* session resolution;
* optimistic versioning;
* idle expiry.

No LLM. No outbound.

### M8.3 — Outbox, policy engine, and status callback

Scope:

* `OutboundMessage`;
* `OutboundStatusEvent`;
* `OutboxService`;
* `OutboundPolicyEngine`;
* `ChannelDispatcher`;
* `MockChannelAdapter`;
* status callback endpoint;
* all safety guards unit-tested.

No real sends.

### M8.4 — Structured LLM turn handler and active sessions UI

Scope:

* `StructuredTurnClient`;
* Anthropic Haiku adapter;
* `TurnOutputSchema`;
* Pydantic validation;
* catalog snapshot/versioning;
* prompt caching;
* malformed-output policy;
* low-confidence policy;
* `LLMCallLog`;
* active sessions UI;
* operator identity dropdown;
* stale-view detection.

Bot can think. Real outbound remains blocked/log-only.

### M8.5 — First real clarification sends

Scope:

* Twilio Sandbox real sends;
* allowlisted number only;
* clarification intents only;
* status callbacks observed end-to-end.

Commitment outbound still blocked.

### M8.6 — Operator-gated commitment

Scope:

* atomic confirmation transaction;
* deterministic commitment rendering;
* valid operator identity required;
* stale version protection;
* failed-send retry flow;
* post-confirm amendment session;
* cost circuit breaker.

### M8.7 — Multi-model and eval scaffolding

Scope:

* OpenAI adapter;
* Gemini adapter;
* capability-aware structured client interface;
* shadow mode;
* eval harness skeleton using logged examples.

Shadow mode is read-only and cannot affect customer state.

### M8.8 — Closure

Scope:

* README update;
* operations runbook;
* Twilio Sandbox setup notes;
* ngrok development flow;
* Railway deployment notes;
* stuck session recovery;
* retry procedures;
* DECISIONS update;
* CHANGELOG update;
* ROADMAP update.

## 20. Deferred to post-M8

Deferred:

* production WhatsApp onboarding;
* Meta business verification;
* WhatsApp templates;
* full 24-hour production messaging policy;
* real authentication;
* selective autonomy;
* payment integration;
* proactive messaging;
* Streamlit-to-web migration;
* customer self-service amendment flow;
* bot metrics dashboard widget;
* full eval harness using real pilot data;
* multi-instance worker scaling.
