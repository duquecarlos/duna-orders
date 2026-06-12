import logging
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


def create_app(
    *,
    app_settings: Settings = settings,
    storage: StorageInterface | None = None,
    parser: ParserInterface | None = None,
    processed_message_store: PostgresProcessedMessageStore | None = None,
    order_lifecycle_store: OrderLifecycleStore | None = None,
    conversation_advancement_service: ConversationAdvancementService | None = None,
    conversation_customer_claim_store: PostgresConversationCustomerClaimStore | None = None,
) -> FastAPI:
    app = FastAPI(title="Duna Orders Webhook")
    app.state.settings = app_settings
    app.state.storage = storage
    app.state.parser = parser
    app.state.processed_message_store = processed_message_store
    app.state.order_lifecycle_store = order_lifecycle_store
    app.state.conversation_advancement_service = conversation_advancement_service
    app.state.conversation_customer_claim_store = conversation_customer_claim_store

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

        claim_store = _get_conversation_customer_claim_store(app)
        holder_id = str(uuid4())
        customer_key = normalize_customer_claim_key(tenant_id, customer_phone)

        if not claim_store.try_acquire(
            tenant_id=tenant_id,
            customer_key=customer_key,
            holder_id=holder_id,
        ):
            logger.info(
                "Conversation claim busy for tenant_id=%s customer_key=%s; "
                "returning 503 for redelivery",
                tenant_id,
                customer_key,
            )
            return Response(status_code=503)

        try:
            is_new_message = _get_processed_message_store(app).try_record_message(
                message_sid=message_sid,
                tenant_id=tenant_id,
                from_number=raw_sender,
                raw_body=inbound_body,
            )

            if not is_new_message:
                return Response(status_code=200)

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
                    received_at=utc_now(),
                    renew_customer_claim=renew_customer_claim,
                )

                if result.resulting_order_id is not None:
                    _get_processed_message_store(app).mark_order_created(
                        message_sid=message_sid,
                        order_id=result.resulting_order_id,
                    )

            return Response(status_code=200)
        finally:
            claim_store.release(
                tenant_id=tenant_id,
                customer_key=customer_key,
                holder_id=holder_id,
            )

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


app = create_app()