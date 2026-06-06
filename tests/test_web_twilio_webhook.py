from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from duna_orders.config import Settings
from duna_orders.domain.models import (
    DraftItemRequest,
    DraftOrderRequest,
    ParseResult,
    Product,
)
from duna_orders.storage.memory import InMemoryStorage
from duna_orders.web.app import create_app
from tests._fakes import MockParser
from tests.conftest import DEFAULT_TEST_TENANT_ID


AUTH_TOKEN = "test-auth-token"
WEBHOOK_PATH = "/webhooks/twilio/whatsapp"
WEBHOOK_URL = f"http://testserver{WEBHOOK_PATH}"


def _settings() -> Settings:
    return Settings(
        duna_storage_backend="memory",
        twilio_auth_token=AUTH_TOKEN,
        twilio_webhook_public_url=None,
        webhook_tenant_id=DEFAULT_TEST_TENANT_ID,
    )


def _seed_product(storage: InMemoryStorage) -> Product:
    product = Product(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        product_id="prd_bandeja",
        product_name="Bandeja paisa",
        aliases=["bandeja"],
        category="platos_fuertes",
        unit="unidad",
        unit_price=Decimal("32000"),
        active=True,
        current_stock=Decimal("10"),
        min_stock=Decimal("1"),
    )
    storage.upsert_product(product)

    return product


def _signed_headers(params: dict[str, str], *, url: str = WEBHOOK_URL) -> dict[str, str]:
    signature = RequestValidator(AUTH_TOKEN).compute_signature(url, params)

    return {
        "X-Twilio-Signature": signature,
        "Content-Type": "application/x-www-form-urlencoded",
    }


def test_health_check_returns_ok() -> None:
    app = create_app(
        app_settings=_settings(),
        storage=InMemoryStorage(),
        parser=MockParser(),
    )
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_twilio_webhook_rejects_missing_signature_before_processing() -> None:
    storage = InMemoryStorage()
    parser = MockParser()
    app = create_app(app_settings=_settings(), storage=storage, parser=parser)
    client = TestClient(app)

    response = client.post(
        WEBHOOK_PATH,
        data={
            "From": "whatsapp:+573001112233",
            "Body": "Buenas, una bandeja paisa",
        },
    )

    assert response.status_code == 403
    assert parser.calls == []
    assert storage.list_orders() == []


def test_twilio_webhook_rejects_invalid_signature_before_processing() -> None:
    storage = InMemoryStorage()
    parser = MockParser()
    app = create_app(app_settings=_settings(), storage=storage, parser=parser)
    client = TestClient(app)

    response = client.post(
        WEBHOOK_PATH,
        data={
            "From": "whatsapp:+573001112233",
            "Body": "Buenas, una bandeja paisa",
        },
        headers={"X-Twilio-Signature": "invalid"},
    )

    assert response.status_code == 403
    assert parser.calls == []
    assert storage.list_orders() == []


def test_twilio_webhook_creates_one_draft_order_from_signed_inbound_message() -> None:
    storage = InMemoryStorage()
    product = _seed_product(storage)
    raw_message = "Buenas, me regala una bandeja paisa para recoger. Pago por Nequi."

    parser = MockParser(
        result=ParseResult(
            request=DraftOrderRequest(
                tenant_id=DEFAULT_TEST_TENANT_ID,
                raw_message=raw_message,
                customer_name="Cliente WhatsApp",
                customer_phone=None,
                fulfillment_type="pickup",
                payment_method="nequi",
                items=[
                    DraftItemRequest(
                        tenant_id=DEFAULT_TEST_TENANT_ID,
                        product_id=product.product_id,
                        quantity=Decimal("1"),
                    )
                ],
            ),
            warnings=[],
            model="mock-parser",
            latency_ms=1,
            raw_response="{}",
        )
    )
    app = create_app(app_settings=_settings(), storage=storage, parser=parser)
    client = TestClient(app)

    params = {
        "From": "whatsapp:+573001112233",
        "Body": raw_message,
    }
    response = client.post(
        WEBHOOK_PATH,
        data=params,
        headers=_signed_headers(params),
    )

    orders = storage.list_orders()

    assert response.status_code == 200
    assert response.text == ""
    assert len(orders) == 1
    assert orders[0].status == "draft"
    assert orders[0].tenant_id == DEFAULT_TEST_TENANT_ID
    assert orders[0].raw_message == raw_message
    assert orders[0].customer_phone_snapshot == "+573001112233"
    assert orders[0].items[0].product_id == product.product_id
    assert storage.list_stock_movements() == []
    assert len(parser.calls) == 1


def test_twilio_webhook_empty_body_returns_200_and_creates_no_order() -> None:
    storage = InMemoryStorage()
    _seed_product(storage)
    parser = MockParser()
    app = create_app(app_settings=_settings(), storage=storage, parser=parser)
    client = TestClient(app)

    params = {
        "From": "whatsapp:+573001112233",
        "Body": "   ",
    }
    response = client.post(
        WEBHOOK_PATH,
        data=params,
        headers=_signed_headers(params),
    )

    assert response.status_code == 200
    assert response.text == ""
    assert parser.calls == []
    assert storage.list_orders() == []