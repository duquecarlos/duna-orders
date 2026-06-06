from urllib.parse import parse_qsl

from fastapi import FastAPI, Request
from fastapi.responses import Response

from duna_orders.config import Settings, settings
from duna_orders.parsing.base import ParserInterface
from duna_orders.storage.base import StorageInterface
from duna_orders.storage.factory import build_storage
from duna_orders.web.inbound import create_draft_from_inbound_message
from duna_orders.web.security import validate_twilio_signature


def create_app(
    *,
    app_settings: Settings = settings,
    storage: StorageInterface | None = None,
    parser: ParserInterface | None = None,
) -> FastAPI:
    app = FastAPI(title="Duna Orders Webhook")
    app.state.settings = app_settings
    app.state.storage = storage
    app.state.parser = parser

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhooks/twilio/whatsapp")
    async def twilio_whatsapp_webhook(request: Request) -> Response:
        body_bytes = await request.body()
        body_text = body_bytes.decode("utf-8")
        form_params = dict(parse_qsl(body_text, keep_blank_values=True))

        validation_url = (
            app_settings.twilio_webhook_public_url
            or str(request.url)
        )
        is_valid = validate_twilio_signature(
            url=validation_url,
            form_params=form_params,
            signature=request.headers.get("X-Twilio-Signature"),
            auth_token=app_settings.twilio_auth_token,
        )

        if not is_valid:
            return Response(status_code=403)

        inbound_body = form_params.get("Body", "")
        sender = form_params.get("From")

        if inbound_body.strip():
            create_draft_from_inbound_message(
                storage=_get_storage(app),
                parser=_get_parser(app),
                tenant_id=app_settings.webhook_tenant_id,
                sender=sender,
                body=inbound_body,
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


app = create_app()