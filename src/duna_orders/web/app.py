import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from urllib.parse import parse_qsl
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import Response

from duna_orders.config import Settings, settings
from duna_orders.domain.models import utc_now
from duna_orders.parsing.base import ParserInterface
from duna_orders.services.conversation_advancement import ConversationAdvancementService
from duna_orders.services.orders import OrderService
from duna_orders.services.parsing import ParsingService
from duna_orders.services.tenant_scoped_reads import TenantScopedReadService
from duna_orders.storage.base import StorageInterface
from duna_orders.storage.conversation_customer_claims import (
    PostgresConversationCustomerClaimStore,
    normalize_customer_claim_key,
)
from duna_orders.storage.conversation_orders import PostgresConversationOrderLookup
from duna_orders.storage.conversation_state import PostgresConversationStateStore
from duna_orders.storage.deferred_inbound import (
    DeferredInboundRecord,
    PostgresDeferredInboundStore,
)
from duna_orders.storage.factory import build_storage
from duna_orders.storage.processed_messages import PostgresProcessedMessageStore
from duna_orders.storage.postgres_session import get_or_create_session_factory
from duna_orders.web.inbound import _twilio_whatsapp_sender_to_phone
from duna_orders.web.security import validate_twilio_signature
from duna_orders.storage.order_lifecycle import (
    OrderLifecycleStore,
    PostgresOrderLifecycleStore,
)
from duna_orders.storage.postgres import PostgresStorage

logger = logging.getLogger(__name__)

# Bounded number of pending deferred_inbound rows for the same customer that
# automatic drain-on-release will replay after a single held claim is
# released. Keeps post-release draining a fast, in-request operation rather
# than an unbounded loop.
AUTO_DRAIN_LIMIT = 5


class ValidatedInboundProcessingOutcome(Enum):
    PROCESSED = "processed"
    DUPLICATE = "duplicate"
    CLAIM_BUSY = "claim_busy"
    DEFERRED = "deferred"


@dataclass(frozen=True)
class ValidatedInboundProcessingResult:
    outcome: ValidatedInboundProcessingOutcome


def _process_validated_inbound_message(
    *,
    app: FastAPI,
    tenant_id: str,
    message_sid: str,
    raw_sender: str,
    customer_phone: str,
    inbound_body: str,
    received_at: datetime,
    auto_drain_after_release: bool = True,
) -> ValidatedInboundProcessingResult:
    claim_store = _get_conversation_customer_claim_store(app)
    holder_id = str(uuid4())
    customer_key = normalize_customer_claim_key(tenant_id, customer_phone)

    if not claim_store.try_acquire(
        tenant_id=tenant_id,
        customer_key=customer_key,
        holder_id=holder_id,
    ):
        logger.info(
            "Conversation claim busy for tenant_id=%s customer_key=%s "
            "message_sid=%s; deferring for later processing",
            tenant_id,
            customer_key,
            message_sid,
        )

        try:
            _get_deferred_inbound_store(app).defer_message(
                message_sid=message_sid,
                tenant_id=tenant_id,
                customer_key=customer_key,
                from_number=raw_sender,
                raw_body=inbound_body,
                received_at=received_at,
            )
        except Exception:
            logger.exception(
                "Failed to defer claim-busy message_sid=%s for tenant_id=%s "
                "customer_key=%s; returning 503 for redelivery",
                message_sid,
                tenant_id,
                customer_key,
            )
            return ValidatedInboundProcessingResult(
                outcome=ValidatedInboundProcessingOutcome.CLAIM_BUSY
            )

        return ValidatedInboundProcessingResult(
            outcome=ValidatedInboundProcessingOutcome.DEFERRED
        )

    try:
        is_new_message = _get_processed_message_store(app).try_record_message(
            message_sid=message_sid,
            tenant_id=tenant_id,
            from_number=raw_sender,
            raw_body=inbound_body,
        )

        if not is_new_message:
            return ValidatedInboundProcessingResult(
                outcome=ValidatedInboundProcessingOutcome.DUPLICATE
            )

        if inbound_body.strip():
            def renew_customer_claim() -> bool:
                return claim_store.renew(
                    tenant_id=tenant_id,
                    customer_key=customer_key,
                    holder_id=holder_id,
                )

            result = _get_conversation_advancement_service(app).advance(
                tenant_id=tenant_id,
                message_sid=message_sid,
                from_number=customer_phone,
                body=inbound_body,
                received_at=received_at,
                renew_customer_claim=renew_customer_claim,
            )

            if result.resulting_order_id is not None:
                _get_processed_message_store(app).mark_order_created(
                    message_sid=message_sid,
                    order_id=result.resulting_order_id,
                )

        return ValidatedInboundProcessingResult(
            outcome=ValidatedInboundProcessingOutcome.PROCESSED
        )
    finally:
        claim_store.release(
            tenant_id=tenant_id,
            customer_key=customer_key,
            holder_id=holder_id,
        )

        if auto_drain_after_release:
            try:
                drain_pending_deferred_inbound_for_customer(
                    app,
                    tenant_id=tenant_id,
                    customer_key=customer_key,
                    limit=AUTO_DRAIN_LIMIT,
                )
            except Exception:
                logger.exception(
                    "Automatic drain-on-release failed for tenant_id=%s "
                    "customer_key=%s; pending deferred_inbound rows remain "
                    "for the next release or manual drain",
                    tenant_id,
                    customer_key,
                )


def create_app(
    *,
    app_settings: Settings = settings,
    storage: StorageInterface | None = None,
    parser: ParserInterface | None = None,
    processed_message_store: PostgresProcessedMessageStore | None = None,
    order_lifecycle_store: OrderLifecycleStore | None = None,
    conversation_advancement_service: ConversationAdvancementService | None = None,
    conversation_customer_claim_store: PostgresConversationCustomerClaimStore | None = None,
    deferred_inbound_store: PostgresDeferredInboundStore | None = None,
) -> FastAPI:
    app = FastAPI(title="Duna Orders Webhook")
    app.state.settings = app_settings
    app.state.storage = storage
    app.state.parser = parser
    app.state.processed_message_store = processed_message_store
    app.state.order_lifecycle_store = order_lifecycle_store
    app.state.conversation_advancement_service = conversation_advancement_service
    app.state.conversation_customer_claim_store = conversation_customer_claim_store
    app.state.deferred_inbound_store = deferred_inbound_store

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhooks/twilio/whatsapp")
    async def twilio_whatsapp_webhook(request: Request) -> Response:
        body_bytes = await request.body()
        body_text = body_bytes.decode("utf-8")
        form_params = dict(parse_qsl(body_text, keep_blank_values=True))

        validation_url = app_settings.twilio_webhook_public_url

        if not validation_url:
            return Response(status_code=500)

        is_valid = validate_twilio_signature(
            url=validation_url,
            form_params=form_params,
            signature=request.headers.get("X-Twilio-Signature"),
            auth_token=app_settings.twilio_auth_token,
        )

        if not is_valid:
            return Response(status_code=403)

        message_sid = form_params.get("MessageSid", "").strip()

        if not message_sid:
            return Response(status_code=400)

        raw_sender = form_params.get("From", "")
        customer_phone = _twilio_whatsapp_sender_to_phone(raw_sender)

        if not customer_phone:
            return Response(status_code=400)

        inbound_body = form_params.get("Body", "")
        tenant_id = app_settings.webhook_tenant_id

        result = _process_validated_inbound_message(
            app=app,
            tenant_id=tenant_id,
            message_sid=message_sid,
            raw_sender=raw_sender,
            customer_phone=customer_phone,
            inbound_body=inbound_body,
            received_at=utc_now(),
        )

        if result.outcome is ValidatedInboundProcessingOutcome.CLAIM_BUSY:
            return Response(status_code=503)

        if result.outcome is ValidatedInboundProcessingOutcome.DEFERRED:
            return Response(status_code=202)

        return Response(status_code=200)

    return app


def _get_storage(app: FastAPI) -> StorageInterface:
    storage = app.state.storage

    if storage is None:
        storage = build_storage(app.state.settings)
        app.state.storage = storage

    return storage


def _get_parser(app: FastAPI) -> ParserInterface:
    parser = app.state.parser

    if parser is None:
        from duna_orders.parsing.anthropic_parser import AnthropicParser

        parser = AnthropicParser()
        app.state.parser = parser

    return parser


def _get_processed_message_store(app: FastAPI) -> PostgresProcessedMessageStore:
    store = app.state.processed_message_store

    if store is None:
        database_url = app.state.settings.database_url

        if not database_url:
            raise RuntimeError("DATABASE_URL is required for webhook idempotency.")

        store = PostgresProcessedMessageStore(
            get_or_create_session_factory(database_url)
        )
        app.state.processed_message_store = store

    return store


def _get_conversation_customer_claim_store(app: FastAPI) -> PostgresConversationCustomerClaimStore:
    store = app.state.conversation_customer_claim_store

    if store is None:
        database_url = app.state.settings.database_url

        if not database_url:
            raise RuntimeError("DATABASE_URL is required for conversation customer claims.")

        store = PostgresConversationCustomerClaimStore(
            get_or_create_session_factory(database_url)
        )
        app.state.conversation_customer_claim_store = store

    return store


def _get_deferred_inbound_store(app: FastAPI) -> PostgresDeferredInboundStore:
    store = app.state.deferred_inbound_store

    if store is None:
        database_url = app.state.settings.database_url

        if not database_url:
            raise RuntimeError("DATABASE_URL is required for deferred inbound storage.")

        store = PostgresDeferredInboundStore(
            get_or_create_session_factory(database_url)
        )
        app.state.deferred_inbound_store = store

    return store


def _get_order_lifecycle_store(app: FastAPI) -> OrderLifecycleStore | None:
    store = app.state.order_lifecycle_store

    if store is not None:
        return store

    storage = _get_storage(app)

    if not isinstance(storage, PostgresStorage):
        return None

    store = PostgresOrderLifecycleStore(storage._session_factory)
    app.state.order_lifecycle_store = store

    return store


def _get_conversation_advancement_service(app: FastAPI) -> ConversationAdvancementService:
    service = app.state.conversation_advancement_service

    if service is not None:
        return service

    storage = _get_storage(app)

    if not isinstance(storage, PostgresStorage):
        raise RuntimeError(
            "Postgres-backed storage is required for conversation advancement."
        )

    session_factory = storage._session_factory
    service = ConversationAdvancementService(
        conversation_state_store=PostgresConversationStateStore(session_factory),
        conversation_order_lookup=PostgresConversationOrderLookup(session_factory),
        scoped_reads=TenantScopedReadService(storage),
        parsing_service=ParsingService(_get_parser(app), storage),
        order_service=OrderService(storage, lifecycle_store=_get_order_lifecycle_store(app)),
    )
    app.state.conversation_advancement_service = service

    return service


@dataclass(frozen=True)
class DeferredInboundDrainSummary:
    processed_message_sids: list[str]
    still_pending_message_sids: list[str]
    failed_message_sids: list[str]


def drain_pending_deferred_inbound(
    app: FastAPI,
    *,
    tenant_id: str,
    limit: int | None = None,
) -> DeferredInboundDrainSummary:
    """Manually reprocess pending deferred_inbound rows for one tenant.

    One-shot, manual-only backstop: re-enters _process_validated_inbound_message
    as a fresh arrival for each pending row across the whole tenant, using the
    trusted stored post-validation artifact (no signature re-validation). Rows
    whose claim is still busy are re-deferred (idempotent) and remain pending.
    """
    deferred_store = _get_deferred_inbound_store(app)
    pending = deferred_store.list_pending_for_tenant(tenant_id=tenant_id, limit=limit)

    return _replay_deferred_records(app, deferred_store, pending, tenant_id=tenant_id)


def drain_pending_deferred_inbound_for_customer(
    app: FastAPI,
    *,
    tenant_id: str,
    customer_key: str,
    limit: int | None = None,
) -> DeferredInboundDrainSummary:
    """Reprocess pending deferred_inbound rows for one tenant+customer.

    Used both as the automatic drain-on-release step (with a small bounded
    `limit`, after the originating request's own claim has been released) and
    as a customer-scoped manual drain. Re-enters
    _process_validated_inbound_message as a fresh arrival for each pending
    row using the trusted stored post-validation artifact (no signature
    re-validation). Rows whose claim is still busy are re-deferred (idempotent)
    and remain pending.
    """
    deferred_store = _get_deferred_inbound_store(app)
    pending = deferred_store.list_pending_for_customer(
        tenant_id=tenant_id, customer_key=customer_key, limit=limit
    )

    return _replay_deferred_records(app, deferred_store, pending, tenant_id=tenant_id)


def _replay_deferred_records(
    app: FastAPI,
    deferred_store: PostgresDeferredInboundStore,
    pending: list[DeferredInboundRecord],
    *,
    tenant_id: str,
) -> DeferredInboundDrainSummary:
    processed: list[str] = []
    still_pending: list[str] = []
    failed: list[str] = []

    for record in pending:
        customer_phone = _twilio_whatsapp_sender_to_phone(record.from_number)

        if not customer_phone:
            logger.warning(
                "Skipping deferred message_sid=%s for tenant_id=%s: could not "
                "derive customer phone from stored from_number",
                record.message_sid,
                tenant_id,
            )
            failed.append(record.message_sid)
            continue

        deferred_store.mark_processing_started(message_sid=record.message_sid)

        try:
            result = _process_validated_inbound_message(
                app=app,
                tenant_id=record.tenant_id,
                message_sid=record.message_sid,
                raw_sender=record.from_number,
                customer_phone=customer_phone,
                inbound_body=record.raw_body,
                received_at=record.received_at,
                auto_drain_after_release=False,
            )
        except Exception:
            logger.exception(
                "Drain failed to reprocess deferred message_sid=%s "
                "for tenant_id=%s",
                record.message_sid,
                tenant_id,
            )
            failed.append(record.message_sid)
            continue

        if result.outcome in (
            ValidatedInboundProcessingOutcome.PROCESSED,
            ValidatedInboundProcessingOutcome.DUPLICATE,
        ):
            deferred_store.mark_processed(message_sid=record.message_sid)
            processed.append(record.message_sid)
        else:
            still_pending.append(record.message_sid)

    return DeferredInboundDrainSummary(
        processed_message_sids=processed,
        still_pending_message_sids=still_pending,
        failed_message_sids=failed,
    )


app = create_app()