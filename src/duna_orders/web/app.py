from urllib.parse import parse_qsl

from fastapi import FastAPI, Request
from fastapi.responses import Response

from duna_orders.config import Settings, settings
from duna_orders.parsing.base import ParserInterface
from duna_orders.storage.base import StorageInterface
from duna_orders.storage.factory import build_storage
from duna_orders.storage.processed_messages import PostgresProcessedMessageStore
from duna_orders.storage.postgres_session import get_or_create_session_factory
from duna_orders.web.inbound import create_draft_from_inbound_message
from duna_orders.web.security import validate_twilio_signature
from duna_orders.storage.order_lifecycle import (
    OrderLifecycleStore,
    PostgresOrderLifecycleStore,
)
from duna_orders.storage.postgres import PostgresStorage

def create_app(
    *,
    app_settings: Settings = settings,
    storage: StorageInterface | None = None,
    parser: ParserInterface | None = None,
    processed_message_store: PostgresProcessedMessageStore | None = None,
    order_lifecycle_store: OrderLifecycleStore | None = None,
) -> FastAPI:
    app = FastAPI(title="Duna Orders Webhook")
    app.state.settings = app_settings
    app.state.storage = storage
    app.state.parser = parser
    app.state.processed_message_store = processed_message_store
    app.state.order_lifecycle_store = order_lifecycle_store

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

        inbound_body = form_params.get("Body", "")
        sender = form_params.get("From")
        tenant_id = app_settings.webhook_tenant_id

        is_new_message = _get_processed_message_store(app).try_record_message(
            message_sid=message_sid,
            tenant_id=tenant_id,
            from_number=sender,
            raw_body=inbound_body,
        )

        if not is_new_message:
            return Response(status_code=200)

        if inbound_body.strip():
            order = create_draft_from_inbound_message(
                storage=_get_storage(app),
                parser=_get_parser(app),
                tenant_id=tenant_id,
                sender=sender,
                body=inbound_body,
                lifecycle_store=_get_order_lifecycle_store(app),
            )

            if order is not None:
                _get_processed_message_store(app).mark_order_created(
                    message_sid=message_sid,
                    order_id=order.order_id,
                )

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
app = create_app()