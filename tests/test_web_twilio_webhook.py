from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from duna_orders.config import Settings
from duna_orders.domain.models import (
    DraftItemRequest,
    DraftOrderRequest,
    ParseResult,
    Product,
    Order,
    OrderStatusTransition,
    utc_now,
)
from duna_orders.services.conversation_advancement import (
    ConversationAdvancementOutcome,
    ConversationAdvancementResult,
)
from duna_orders.storage.base import StorageInterface
from duna_orders.storage.conversation_customer_claims import (
    DEFAULT_CLAIM_LEASE_DURATION,
    normalize_customer_claim_key,
)
from duna_orders.storage.conversation_state import PostgresConversationStateStore
from duna_orders.storage.memory import InMemoryStorage
from duna_orders.storage.postgres import PostgresStorage
from duna_orders.storage.postgres_base import Base
from duna_orders.storage.postgres_session import make_engine, make_session_factory
from duna_orders.storage.processed_messages import PostgresProcessedMessageStore
from duna_orders.web.app import (
    ValidatedInboundProcessingOutcome,
    _process_validated_inbound_message,
    create_app,
)
from tests._fakes import MockParser
from tests.conftest import DEFAULT_TEST_TENANT_ID
from duna_orders.parsing.exceptions import ParserError

AUTH_TOKEN = "test-auth-token"
WEBHOOK_PATH = "/webhooks/twilio/whatsapp"
PUBLIC_WEBHOOK_URL = f"https://duna.example.test{WEBHOOK_PATH}"


def _settings() -> Settings:
    return Settings(
        duna_storage_backend="memory",
        twilio_auth_token=AUTH_TOKEN,
        twilio_webhook_public_url=PUBLIC_WEBHOOK_URL,
        webhook_tenant_id=DEFAULT_TEST_TENANT_ID,
    )


def _seed_product(storage: StorageInterface) -> Product:
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


def _seed_other_tenant_product(storage: StorageInterface) -> Product:
    product = Product(
        tenant_id="other-tenant",
        product_id="prd_other_tenant",
        product_name="Other tenant product",
        aliases=["other"],
        category="platos_fuertes",
        unit="unidad",
        unit_price=Decimal("99000"),
        active=True,
        current_stock=Decimal("10"),
        min_stock=Decimal("1"),
    )
    storage.upsert_product(product)

    return product


def _processed_message_store(tmp_path: Path) -> PostgresProcessedMessageStore:
    database_path = tmp_path / "processed_messages_webhook.db"
    engine = make_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)

    return PostgresProcessedMessageStore(make_session_factory(engine))


def _postgres_storage(tmp_path: Path, name: str = "storage") -> PostgresStorage:
    database_path = tmp_path / f"{name}.db"
    engine = make_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)

    return PostgresStorage(make_session_factory(engine))


class FakeLifecycleStore:
    def __init__(self, storage: StorageInterface) -> None:
        self.storage = storage
        self.transitions: list[OrderStatusTransition] = []

    def create_order_with_transition(
        self,
        *,
        order: Order,
        transition: OrderStatusTransition,
    ) -> Order:
        created = self.storage.create_order(order)
        self.transitions.append(transition)
        return created

    def update_order_status_with_transition(
        self,
        *,
        order_id: str,
        status: str,
        transition: OrderStatusTransition,
        confirmed_at=None,
        status_updated_at=None,
    ) -> Order:
        updated = self.storage.update_order_status(
            order_id,
            status,
            confirmed_at=confirmed_at,
            status_updated_at=status_updated_at,
        )
        self.transitions.append(transition)
        return updated

    def list_order_status_transitions(
        self,
        *,
        order_id: str,
        tenant_id: str,
    ) -> list[OrderStatusTransition]:
        return [
            transition
            for transition in self.transitions
            if transition.order_id == order_id and transition.tenant_id == tenant_id
        ]


@dataclass
class _AdvanceCall:
    tenant_id: str
    message_sid: str
    from_number: str
    body: str
    received_at: datetime
    renew_customer_claim: Callable[[], bool] | None = None


class FakeConversationAdvancementService:
    """Spy double standing in for ConversationAdvancementService in webhook tests."""

    def __init__(self, result: ConversationAdvancementResult | None = None) -> None:
        self.calls: list[_AdvanceCall] = []
        self._result = result or ConversationAdvancementResult(
            outcome=ConversationAdvancementOutcome.TURN_APPENDED_INCOMPLETE,
            conversation_id="conv_fake",
            turn_appended=True,
            draft_created=False,
            resulting_order_id=None,
        )

    def advance(
        self,
        *,
        tenant_id: str,
        message_sid: str,
        from_number: str,
        body: str,
        received_at: datetime,
        renew_customer_claim: Callable[[], bool] | None = None,
    ) -> ConversationAdvancementResult:
        self.calls.append(
            _AdvanceCall(
                tenant_id=tenant_id,
                message_sid=message_sid,
                from_number=from_number,
                body=body,
                received_at=received_at,
                renew_customer_claim=renew_customer_claim,
            )
        )
        return self._result


class FakeConversationCustomerClaimStore:
    """In-memory ConversationCustomerClaimStore double for webhook tests."""

    def __init__(self) -> None:
        self.held: dict[tuple[str, str], str] = {}
        self.acquire_calls: list[tuple[str, str, str]] = []
        self.release_calls: list[tuple[str, str, str]] = []
        self.renew_calls: list[tuple[str, str, str]] = []

    def try_acquire(
        self,
        *,
        tenant_id: str,
        customer_key: str,
        holder_id: str,
        lease_duration: timedelta = DEFAULT_CLAIM_LEASE_DURATION,
    ) -> bool:
        self.acquire_calls.append((tenant_id, customer_key, holder_id))
        key = (tenant_id, customer_key)

        if key in self.held:
            return False

        self.held[key] = holder_id
        return True

    def release(self, *, tenant_id: str, customer_key: str, holder_id: str) -> bool:
        self.release_calls.append((tenant_id, customer_key, holder_id))
        key = (tenant_id, customer_key)

        if self.held.get(key) == holder_id:
            del self.held[key]
            return True

        return False

    def renew(
        self,
        *,
        tenant_id: str,
        customer_key: str,
        holder_id: str,
        lease_duration: timedelta = DEFAULT_CLAIM_LEASE_DURATION,
    ) -> bool:
        self.renew_calls.append((tenant_id, customer_key, holder_id))
        return self.held.get((tenant_id, customer_key)) == holder_id


def _create_app(**kwargs: object) -> FastAPI:
    """Build the webhook app, defaulting an isolated claim-store double.

    Individual tests that need to observe or pre-seed claim acquisition can
    still pass their own `conversation_customer_claim_store=...`.
    """
    kwargs.setdefault(
        "conversation_customer_claim_store", FakeConversationCustomerClaimStore()
    )
    return create_app(**kwargs)  # type: ignore[arg-type]


def _signed_headers(
    params: dict[str, str],
    *,
    url: str = PUBLIC_WEBHOOK_URL,
) -> dict[str, str]:
    signature = RequestValidator(AUTH_TOKEN).compute_signature(url, params)

    return {
        "X-Twilio-Signature": signature,
        "Content-Type": "application/x-www-form-urlencoded",
    }


def _parse_result_for_product(product: Product, raw_message: str) -> ParseResult:
    return ParseResult(
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


def _order_snapshot(order: Order) -> tuple[object, ...]:
    return (
        order.order_id,
        order.status,
        order.raw_message,
        order.conversation_id,
        order.total,
        tuple((item.product_id, item.quantity) for item in order.items),
    )


def test_health_check_returns_ok() -> None:
    app = _create_app(
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
    fake_service = FakeConversationAdvancementService()
    app = _create_app(
        app_settings=_settings(),
        storage=storage,
        parser=parser,
        conversation_advancement_service=fake_service,
    )
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
    assert fake_service.calls == []


def test_twilio_webhook_rejects_invalid_signature_before_processing() -> None:
    storage = InMemoryStorage()
    parser = MockParser()
    fake_service = FakeConversationAdvancementService()
    app = _create_app(
        app_settings=_settings(),
        storage=storage,
        parser=parser,
        conversation_advancement_service=fake_service,
    )
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
    assert fake_service.calls == []


def test_twilio_webhook_invalid_signature_does_not_record_processed_message(
    tmp_path: Path,
) -> None:
    storage = InMemoryStorage()
    parser = MockParser()
    fake_service = FakeConversationAdvancementService()
    processed_store = _processed_message_store(tmp_path)
    app = _create_app(
        app_settings=_settings(),
        storage=storage,
        parser=parser,
        processed_message_store=processed_store,
        conversation_advancement_service=fake_service,
    )
    client = TestClient(app)

    params = {
        "MessageSid": "SM_INVALID_SIGNATURE",
        "From": "whatsapp:+573001112233",
        "Body": "Buenas, una bandeja paisa",
    }

    response = client.post(
        WEBHOOK_PATH,
        data=params,
        headers={"X-Twilio-Signature": "invalid"},
    )

    assert response.status_code == 403
    assert processed_store.get_message("SM_INVALID_SIGNATURE") is None
    assert parser.calls == []
    assert storage.list_orders() == []
    assert fake_service.calls == []


def test_twilio_webhook_rejects_missing_message_sid() -> None:
    storage = InMemoryStorage()
    fake_service = FakeConversationAdvancementService()
    app = _create_app(
        app_settings=_settings(),
        storage=storage,
        conversation_advancement_service=fake_service,
    )
    client = TestClient(app)

    params = {
        "From": "whatsapp:+573001112233",
        "Body": "Buenas, una bandeja paisa",
    }

    response = client.post(
        WEBHOOK_PATH,
        data=params,
        headers=_signed_headers(params),
    )

    assert response.status_code == 400
    assert fake_service.calls == []


def test_twilio_webhook_rejects_missing_from_field(tmp_path: Path) -> None:
    storage = InMemoryStorage()
    fake_service = FakeConversationAdvancementService()
    processed_store = _processed_message_store(tmp_path)
    app = _create_app(
        app_settings=_settings(),
        storage=storage,
        processed_message_store=processed_store,
        conversation_advancement_service=fake_service,
    )
    client = TestClient(app)

    params = {
        "MessageSid": "SM_MISSING_FROM",
        "Body": "Buenas, una bandeja paisa",
    }

    response = client.post(
        WEBHOOK_PATH,
        data=params,
        headers=_signed_headers(params),
    )

    assert response.status_code == 400
    assert fake_service.calls == []
    assert processed_store.get_message("SM_MISSING_FROM") is None


def test_twilio_webhook_validates_against_configured_public_url(
    tmp_path: Path,
) -> None:
    storage = InMemoryStorage()
    parser = MockParser()
    fake_service = FakeConversationAdvancementService()
    app = _create_app(
        app_settings=_settings(),
        storage=storage,
        parser=parser,
        processed_message_store=_processed_message_store(tmp_path),
        conversation_advancement_service=fake_service,
    )
    client = TestClient(app)

    params = {
        "MessageSid": "SM_PUBLIC_URL",
        "From": "whatsapp:+573001112233",
        "Body": "Buenas, una bandeja paisa",
    }

    response = client.post(
        WEBHOOK_PATH,
        data=params,
        headers=_signed_headers(params, url=PUBLIC_WEBHOOK_URL),
    )

    assert response.status_code == 200
    assert len(fake_service.calls) == 1


def test_twilio_webhook_returns_500_when_public_url_is_missing() -> None:
    storage = InMemoryStorage()
    parser = MockParser()
    fake_service = FakeConversationAdvancementService()
    settings = _settings().model_copy(update={"twilio_webhook_public_url": None})
    app = _create_app(
        app_settings=settings,
        storage=storage,
        parser=parser,
        conversation_advancement_service=fake_service,
    )
    client = TestClient(app)

    params = {
        "MessageSid": "SM_MISSING_PUBLIC_URL",
        "From": "whatsapp:+573001112233",
        "Body": "Buenas, una bandeja paisa",
    }

    response = client.post(
        WEBHOOK_PATH,
        data=params,
        headers=_signed_headers(params, url=PUBLIC_WEBHOOK_URL),
    )

    assert response.status_code == 500
    assert parser.calls == []
    assert storage.list_orders() == []
    assert fake_service.calls == []


def test_twilio_webhook_creates_one_draft_order_from_signed_inbound_message(
    tmp_path: Path,
) -> None:
    storage = _postgres_storage(tmp_path)
    product = _seed_product(storage)
    raw_message = (
        "Buenas, me regala una bandeja paisa para recoger. Pago por Nequi. "
        + "Detalle adicional sin truncar. " * 25
    )
    processed_store = _processed_message_store(tmp_path)

    parser = MockParser(result=_parse_result_for_product(product, raw_message))
    claim_store = FakeConversationCustomerClaimStore()
    app = _create_app(
        app_settings=_settings(),
        storage=storage,
        parser=parser,
        processed_message_store=processed_store,
        conversation_customer_claim_store=claim_store,
    )
    client = TestClient(app)

    params = {
        "MessageSid": "SM_HAPPY_PATH",
        "From": "whatsapp:+573001112233",
        "Body": raw_message,
    }
    response = client.post(
        WEBHOOK_PATH,
        data=params,
        headers=_signed_headers(params),
    )

    orders = storage.list_orders()
    record = processed_store.get_message("SM_HAPPY_PATH")
    transcript = f"Customer message 1:\n{raw_message}"

    assert response.status_code == 200
    assert response.text == ""
    assert len(orders) == 1
    assert orders[0].status == "draft"
    assert orders[0].tenant_id == DEFAULT_TEST_TENANT_ID
    assert orders[0].conversation_id is not None
    assert orders[0].raw_message == transcript
    assert orders[0].customer_phone_snapshot == "+573001112233"
    assert orders[0].items[0].product_id == product.product_id
    assert storage.list_stock_movements() == []
    assert len(parser.calls) == 1
    assert parser.calls[0][0] == transcript
    assert record is not None
    assert record.raw_body == raw_message
    assert record.from_number == "whatsapp:+573001112233"
    assert len(record.raw_body) > 500
    assert record.resulting_order_id == orders[0].order_id

    # The claim is acquired before processing, renewed once by the real
    # advancement service after parsing (before the draft write), and
    # released in the webhook's finally block once processing completes.
    assert len(claim_store.acquire_calls) == 1
    assert len(claim_store.release_calls) == 1
    assert claim_store.acquire_calls[0] == claim_store.release_calls[0]
    assert claim_store.renew_calls == [claim_store.acquire_calls[0]]
    assert claim_store.held == {}


def test_twilio_webhook_parser_product_context_is_tenant_scoped(
    tmp_path: Path,
) -> None:
    storage = _postgres_storage(tmp_path)
    product = _seed_product(storage)
    other_product = _seed_other_tenant_product(storage)
    raw_message = "Buenas, una bandeja paisa"
    processed_store = _processed_message_store(tmp_path)

    parser = MockParser(result=_parse_result_for_product(product, raw_message))
    app = _create_app(
        app_settings=_settings(),
        storage=storage,
        parser=parser,
        processed_message_store=processed_store,
    )
    client = TestClient(app)

    params = {
        "MessageSid": "SM_TENANT_SCOPED_PRODUCTS",
        "From": "whatsapp:+573001112233",
        "Body": raw_message,
    }
    response = client.post(
        WEBHOOK_PATH,
        data=params,
        headers=_signed_headers(params),
    )

    assert response.status_code == 200
    assert len(parser.calls) == 1
    product_context = parser.calls[0][1]
    assert [context_product.product_id for context_product in product_context] == [
        product.product_id
    ]
    assert other_product.product_id not in [
        context_product.product_id for context_product in product_context
    ]


def test_twilio_webhook_empty_body_returns_200_and_creates_no_order(
    tmp_path: Path,
) -> None:
    storage = InMemoryStorage()
    parser = MockParser()
    fake_service = FakeConversationAdvancementService()
    processed_store = _processed_message_store(tmp_path)
    app = _create_app(
        app_settings=_settings(),
        storage=storage,
        parser=parser,
        processed_message_store=processed_store,
        conversation_advancement_service=fake_service,
    )
    client = TestClient(app)

    params = {
        "MessageSid": "SM_EMPTY_BODY",
        "From": "whatsapp:+573001112233",
        "Body": "   ",
    }
    response = client.post(
        WEBHOOK_PATH,
        data=params,
        headers=_signed_headers(params),
    )

    record = processed_store.get_message("SM_EMPTY_BODY")

    assert response.status_code == 200
    assert response.text == ""
    assert parser.calls == []
    assert fake_service.calls == []
    assert storage.list_orders() == []
    assert record is not None
    assert record.raw_body == "   "
    assert record.from_number == "whatsapp:+573001112233"
    assert record.resulting_order_id is None


def test_twilio_webhook_duplicate_message_sid_creates_only_one_draft(
    tmp_path: Path,
) -> None:
    storage = _postgres_storage(tmp_path)
    product = _seed_product(storage)
    raw_message = "Buenas, una bandeja paisa"

    parser = MockParser(result=_parse_result_for_product(product, raw_message))
    app = _create_app(
        app_settings=_settings(),
        storage=storage,
        parser=parser,
        processed_message_store=_processed_message_store(tmp_path),
    )
    client = TestClient(app)

    params = {
        "MessageSid": "SM_DUPLICATE_WEBHOOK",
        "From": "whatsapp:+573001112233",
        "Body": raw_message,
    }
    headers = _signed_headers(params)

    first = client.post(WEBHOOK_PATH, data=params, headers=headers)
    second = client.post(WEBHOOK_PATH, data=params, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(storage.list_orders()) == 1
    assert len(parser.calls) == 1


def test_twilio_webhook_distinct_customers_create_distinct_drafts(
    tmp_path: Path,
) -> None:
    storage = _postgres_storage(tmp_path)
    product = _seed_product(storage)
    raw_message = "Buenas, una bandeja paisa"

    parser = MockParser(result=_parse_result_for_product(product, raw_message))
    app = _create_app(
        app_settings=_settings(),
        storage=storage,
        parser=parser,
        processed_message_store=_processed_message_store(tmp_path),
    )
    client = TestClient(app)

    first_params = {
        "MessageSid": "SM_DISTINCT_1",
        "From": "whatsapp:+573001112233",
        "Body": raw_message,
    }
    second_params = {
        "MessageSid": "SM_DISTINCT_2",
        "From": "whatsapp:+573009998877",
        "Body": raw_message,
    }

    first = client.post(
        WEBHOOK_PATH,
        data=first_params,
        headers=_signed_headers(first_params),
    )
    second = client.post(
        WEBHOOK_PATH,
        data=second_params,
        headers=_signed_headers(second_params),
    )

    orders = storage.list_orders()

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(orders) == 2
    assert len(parser.calls) == 2
    assert {order.customer_phone_snapshot for order in orders} == {
        "+573001112233",
        "+573009998877",
    }
    assert len({order.conversation_id for order in orders}) == 2


def test_twilio_webhook_followup_message_after_draft_created_links_existing_order(
    tmp_path: Path,
) -> None:
    storage = _postgres_storage(tmp_path)
    product = _seed_product(storage)
    raw_message = "Buenas, una bandeja paisa"
    processed_store = _processed_message_store(tmp_path)

    parser = MockParser(result=_parse_result_for_product(product, raw_message))
    app = _create_app(
        app_settings=_settings(),
        storage=storage,
        parser=parser,
        processed_message_store=processed_store,
    )
    client = TestClient(app)

    first_params = {
        "MessageSid": "SM_FOLLOWUP_1",
        "From": "whatsapp:+573001112233",
        "Body": raw_message,
    }
    second_params = {
        "MessageSid": "SM_FOLLOWUP_2",
        "From": "whatsapp:+573001112233",
        "Body": "Gracias, ya pague",
    }

    first = client.post(
        WEBHOOK_PATH,
        data=first_params,
        headers=_signed_headers(first_params),
    )

    orders_after_first = storage.list_orders()
    assert len(orders_after_first) == 1
    draft_snapshot = _order_snapshot(orders_after_first[0])

    second = client.post(
        WEBHOOK_PATH,
        data=second_params,
        headers=_signed_headers(second_params),
    )

    orders = storage.list_orders()
    first_record = processed_store.get_message("SM_FOLLOWUP_1")
    second_record = processed_store.get_message("SM_FOLLOWUP_2")

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(orders) == 1
    assert len(parser.calls) == 1
    assert first_record is not None
    assert second_record is not None
    assert first_record.resulting_order_id == orders[0].order_id
    assert second_record.resulting_order_id == orders[0].order_id

    # The follow-up message must not mutate the existing draft order.
    assert _order_snapshot(orders[0]) == draft_snapshot

    # Replaying the follow-up MessageSid after draft_created must not reprocess.
    duplicate = client.post(
        WEBHOOK_PATH,
        data=second_params,
        headers=_signed_headers(second_params),
    )

    orders_after_duplicate = storage.list_orders()

    assert duplicate.status_code == 200
    assert len(orders_after_duplicate) == 1
    assert len(parser.calls) == 1
    assert _order_snapshot(orders_after_duplicate[0]) == draft_snapshot


def test_twilio_webhook_tenant_isolation_same_customer_creates_separate_conversations_and_drafts(
    tmp_path: Path,
) -> None:
    storage = _postgres_storage(tmp_path)
    product_a = _seed_product(storage)
    product_b = _seed_other_tenant_product(storage)
    processed_store = _processed_message_store(tmp_path)

    raw_message_a = "Buenas, una bandeja paisa"
    raw_message_b = "Quiero el producto del otro tenant"

    parser_a = MockParser(result=_parse_result_for_product(product_a, raw_message_a))
    parser_b = MockParser(result=_parse_result_for_product(product_b, raw_message_b))

    app_a = _create_app(
        app_settings=_settings(),
        storage=storage,
        parser=parser_a,
        processed_message_store=processed_store,
    )
    app_b = _create_app(
        app_settings=_settings().model_copy(update={"webhook_tenant_id": "other-tenant"}),
        storage=storage,
        parser=parser_b,
        processed_message_store=processed_store,
    )
    client_a = TestClient(app_a)
    client_b = TestClient(app_b)

    customer_from = "whatsapp:+573001112233"
    customer_phone = "+573001112233"

    params_a = {
        "MessageSid": "SM_TENANT_A",
        "From": customer_from,
        "Body": raw_message_a,
    }
    params_b = {
        "MessageSid": "SM_TENANT_B",
        "From": customer_from,
        "Body": raw_message_b,
    }

    response_a = client_a.post(
        WEBHOOK_PATH,
        data=params_a,
        headers=_signed_headers(params_a),
    )
    response_b = client_b.post(
        WEBHOOK_PATH,
        data=params_b,
        headers=_signed_headers(params_b),
    )

    assert response_a.status_code == 200
    assert response_b.status_code == 200

    orders = storage.list_orders()
    assert len(orders) == 2
    assert {order.tenant_id for order in orders} == {
        DEFAULT_TEST_TENANT_ID,
        "other-tenant",
    }
    assert len({order.conversation_id for order in orders}) == 2

    order_a = next(order for order in orders if order.tenant_id == DEFAULT_TEST_TENANT_ID)
    order_b = next(order for order in orders if order.tenant_id == "other-tenant")
    assert [item.product_id for item in order_a.items] == [product_a.product_id]
    assert [item.product_id for item in order_b.items] == [product_b.product_id]

    conversation_state_store = PostgresConversationStateStore(storage._session_factory)
    latest_a = conversation_state_store.get_latest_session_for_customer(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        customer_phone=customer_phone,
    )
    latest_b = conversation_state_store.get_latest_session_for_customer(
        tenant_id="other-tenant",
        customer_phone=customer_phone,
    )
    assert latest_a is not None
    assert latest_a.conversation_id == order_a.conversation_id
    assert latest_b is not None
    assert latest_b.conversation_id == order_b.conversation_id


def test_twilio_webhook_empty_body_retry_records_sid_once_and_creates_no_order(
    tmp_path: Path,
) -> None:
    storage = InMemoryStorage()
    parser = MockParser()
    fake_service = FakeConversationAdvancementService()
    processed_store = _processed_message_store(tmp_path)
    app = _create_app(
        app_settings=_settings(),
        storage=storage,
        parser=parser,
        processed_message_store=processed_store,
        conversation_advancement_service=fake_service,
    )
    client = TestClient(app)

    params = {
        "MessageSid": "SM_EMPTY_RETRY",
        "From": "whatsapp:+573001112233",
        "Body": "   ",
    }
    headers = _signed_headers(params)

    first = client.post(WEBHOOK_PATH, data=params, headers=headers)
    second = client.post(WEBHOOK_PATH, data=params, headers=headers)

    record = processed_store.get_message("SM_EMPTY_RETRY")

    assert first.status_code == 200
    assert second.status_code == 200
    assert parser.calls == []
    assert fake_service.calls == []
    assert storage.list_orders() == []
    assert record is not None
    assert record.raw_body == "   "
    assert record.from_number == "whatsapp:+573001112233"
    assert record.resulting_order_id is None


def test_twilio_webhook_existing_message_sid_returns_200_without_reprocessing(
    tmp_path: Path,
) -> None:
    storage = InMemoryStorage()
    _seed_product(storage)
    parser = MockParser()
    fake_service = FakeConversationAdvancementService()
    processed_store = _processed_message_store(tmp_path)
    processed_store.try_record_message(
        message_sid="SM_ALREADY_SEEN",
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )
    claim_store = FakeConversationCustomerClaimStore()
    app = _create_app(
        app_settings=_settings(),
        storage=storage,
        parser=parser,
        processed_message_store=processed_store,
        conversation_advancement_service=fake_service,
        conversation_customer_claim_store=claim_store,
    )
    client = TestClient(app)

    params = {
        "MessageSid": "SM_ALREADY_SEEN",
        "From": "whatsapp:+573001112233",
        "Body": "Buenas, una bandeja paisa",
    }

    response = client.post(
        WEBHOOK_PATH,
        data=params,
        headers=_signed_headers(params),
    )

    assert response.status_code == 200
    assert parser.calls == []
    assert storage.list_orders() == []
    assert fake_service.calls == []

    # A genuine duplicate is only detected once the claim is held, so it
    # still does one acquire/release round-trip without advancing.
    assert len(claim_store.acquire_calls) == 1
    assert len(claim_store.release_calls) == 1
    assert claim_store.acquire_calls[0] == claim_store.release_calls[0]
    assert claim_store.renew_calls == []
    assert claim_store.held == {}


def test_twilio_webhook_parser_failure_preserves_raw_message_and_creates_no_order(
    tmp_path: Path,
) -> None:
    storage = _postgres_storage(tmp_path)
    raw_message = "Buenas, una bandeja paisa que el parser no puede procesar."
    parser = MockParser(raise_error=ParserError("mock parser failure"))
    processed_store = _processed_message_store(tmp_path)
    claim_store = FakeConversationCustomerClaimStore()
    app = _create_app(
        app_settings=_settings(),
        storage=storage,
        parser=parser,
        processed_message_store=processed_store,
        conversation_customer_claim_store=claim_store,
    )
    client = TestClient(app)

    params = {
        "MessageSid": "SM_PARSE_FAILURE",
        "From": "whatsapp:+573001112233",
        "Body": raw_message,
    }

    response = client.post(
        WEBHOOK_PATH,
        data=params,
        headers=_signed_headers(params),
    )

    record = processed_store.get_message("SM_PARSE_FAILURE")

    assert response.status_code == 200
    assert response.text == ""
    assert len(parser.calls) == 1
    assert storage.list_orders() == []
    assert record is not None
    assert record.raw_body == raw_message
    assert record.from_number == "whatsapp:+573001112233"
    assert record.resulting_order_id is None

    # ParserError short-circuits before the renew check, but release still
    # runs in the webhook's finally block.
    assert len(claim_store.acquire_calls) == 1
    assert len(claim_store.release_calls) == 1
    assert claim_store.acquire_calls[0] == claim_store.release_calls[0]
    assert claim_store.renew_calls == []
    assert claim_store.held == {}


def test_twilio_webhook_uses_injected_lifecycle_store_for_draft_creation(
    tmp_path: Path,
) -> None:
    storage = _postgres_storage(tmp_path)
    product = _seed_product(storage)
    raw_message = "Buenas, una bandeja paisa"
    processed_store = _processed_message_store(tmp_path)
    lifecycle_store = FakeLifecycleStore(storage)

    parser = MockParser(result=_parse_result_for_product(product, raw_message))
    app = _create_app(
        app_settings=_settings(),
        storage=storage,
        parser=parser,
        processed_message_store=processed_store,
        order_lifecycle_store=lifecycle_store,
    )
    client = TestClient(app)

    params = {
        "MessageSid": "SM_LIFECYCLE_DRAFT",
        "From": "whatsapp:+573001112233",
        "Body": raw_message,
    }

    response = client.post(
        WEBHOOK_PATH,
        data=params,
        headers=_signed_headers(params),
    )

    orders = storage.list_orders()
    record = processed_store.get_message("SM_LIFECYCLE_DRAFT")

    assert response.status_code == 200
    assert len(orders) == 1
    assert record is not None
    assert record.resulting_order_id == orders[0].order_id
    assert len(lifecycle_store.transitions) == 1
    assert lifecycle_store.transitions[0].from_status is None
    assert lifecycle_store.transitions[0].to_status == "draft"
    assert lifecycle_store.transitions[0].source == "system"
    assert lifecycle_store.transitions[0].tenant_id == DEFAULT_TEST_TENANT_ID


def test_twilio_webhook_calls_advancement_service_exactly_once_for_new_message(
    tmp_path: Path,
) -> None:
    storage = InMemoryStorage()
    fake_service = FakeConversationAdvancementService()
    processed_store = _processed_message_store(tmp_path)
    app = _create_app(
        app_settings=_settings(),
        storage=storage,
        processed_message_store=processed_store,
        conversation_advancement_service=fake_service,
    )
    client = TestClient(app)

    raw_message = "Buenas, una bandeja paisa"
    params = {
        "MessageSid": "SM_ADVANCE_ONCE",
        "From": "whatsapp:+573001112233",
        "Body": raw_message,
    }

    response = client.post(
        WEBHOOK_PATH,
        data=params,
        headers=_signed_headers(params),
    )

    record = processed_store.get_message("SM_ADVANCE_ONCE")

    assert response.status_code == 200
    assert len(fake_service.calls) == 1

    call = fake_service.calls[0]
    assert call.tenant_id == DEFAULT_TEST_TENANT_ID
    assert call.message_sid == "SM_ADVANCE_ONCE"
    assert call.from_number == "+573001112233"
    assert call.body == raw_message
    assert call.received_at.tzinfo is not None
    assert record is not None
    assert record.resulting_order_id is None


@pytest.mark.parametrize(
    ("outcome", "turn_appended", "draft_created", "resulting_order_id"),
    [
        (
            ConversationAdvancementOutcome.TURN_APPENDED_INCOMPLETE,
            True,
            False,
            None,
        ),
        (
            ConversationAdvancementOutcome.PARSE_INCOMPLETE,
            True,
            False,
            None,
        ),
        (
            ConversationAdvancementOutcome.DRAFT_CREATED,
            True,
            True,
            "ord_fake_created",
        ),
        (
            ConversationAdvancementOutcome.ALREADY_HAS_DRAFT,
            True,
            False,
            "ord_fake_existing",
        ),
        (
            ConversationAdvancementOutcome.DUPLICATE_MESSAGE,
            False,
            False,
            None,
        ),
    ],
)
def test_twilio_webhook_outcome_returns_200_without_outbound_and_links_order(
    tmp_path: Path,
    outcome: ConversationAdvancementOutcome,
    turn_appended: bool,
    draft_created: bool,
    resulting_order_id: str | None,
) -> None:
    storage = InMemoryStorage()
    result = ConversationAdvancementResult(
        outcome=outcome,
        conversation_id="conv_fake",
        turn_appended=turn_appended,
        draft_created=draft_created,
        resulting_order_id=resulting_order_id,
    )
    fake_service = FakeConversationAdvancementService(result=result)
    processed_store = _processed_message_store(tmp_path)
    claim_store = FakeConversationCustomerClaimStore()
    app = _create_app(
        app_settings=_settings(),
        storage=storage,
        processed_message_store=processed_store,
        conversation_advancement_service=fake_service,
        conversation_customer_claim_store=claim_store,
    )
    client = TestClient(app)

    message_sid = f"SM_OUTCOME_{outcome.value}"
    params = {
        "MessageSid": message_sid,
        "From": "whatsapp:+573001112233",
        "Body": "Buenas, una bandeja paisa",
    }

    response = client.post(
        WEBHOOK_PATH,
        data=params,
        headers=_signed_headers(params),
    )

    record = processed_store.get_message(message_sid)

    assert response.status_code == 200
    assert response.text == ""
    assert len(fake_service.calls) == 1
    assert record is not None
    assert record.resulting_order_id == resulting_order_id

    # Regardless of the advancement outcome, the claim acquired for this
    # request is released exactly once in the webhook's finally block.
    assert len(claim_store.acquire_calls) == 1
    assert len(claim_store.release_calls) == 1
    assert claim_store.acquire_calls[0] == claim_store.release_calls[0]
    assert claim_store.held == {}


def test_twilio_webhook_claim_busy_returns_503_without_processing(
    tmp_path: Path,
) -> None:
    storage = InMemoryStorage()
    parser = MockParser()
    fake_service = FakeConversationAdvancementService()
    processed_store = _processed_message_store(tmp_path)
    claim_store = FakeConversationCustomerClaimStore()

    tenant_id = DEFAULT_TEST_TENANT_ID
    customer_key = normalize_customer_claim_key(tenant_id, "+573001112233")
    assert claim_store.try_acquire(
        tenant_id=tenant_id,
        customer_key=customer_key,
        holder_id="other-holder",
    )

    app = _create_app(
        app_settings=_settings(),
        storage=storage,
        parser=parser,
        processed_message_store=processed_store,
        conversation_advancement_service=fake_service,
        conversation_customer_claim_store=claim_store,
    )
    client = TestClient(app)

    params = {
        "MessageSid": "SM_CLAIM_BUSY",
        "From": "whatsapp:+573001112233",
        "Body": "Buenas, una bandeja paisa",
    }

    response = client.post(
        WEBHOOK_PATH,
        data=params,
        headers=_signed_headers(params),
    )

    assert response.status_code == 503
    assert processed_store.get_message("SM_CLAIM_BUSY") is None
    assert parser.calls == []
    assert fake_service.calls == []
    assert storage.list_orders() == []

    # Nothing was acquired by this request, so nothing is released, and the
    # other holder's claim is left untouched.
    assert claim_store.release_calls == []
    assert claim_store.held[(tenant_id, customer_key)] == "other-holder"


def test_twilio_webhook_genuine_duplicate_does_extra_claim_round_trip_without_advancing(
    tmp_path: Path,
) -> None:
    storage = InMemoryStorage()
    _seed_product(storage)
    parser = MockParser()
    fake_service = FakeConversationAdvancementService()
    processed_store = _processed_message_store(tmp_path)
    processed_store.try_record_message(
        message_sid="SM_DUPLICATE_CLAIM",
        tenant_id=DEFAULT_TEST_TENANT_ID,
    )
    claim_store = FakeConversationCustomerClaimStore()
    app = _create_app(
        app_settings=_settings(),
        storage=storage,
        parser=parser,
        processed_message_store=processed_store,
        conversation_advancement_service=fake_service,
        conversation_customer_claim_store=claim_store,
    )
    client = TestClient(app)

    params = {
        "MessageSid": "SM_DUPLICATE_CLAIM",
        "From": "whatsapp:+573001112233",
        "Body": "Buenas, una bandeja paisa",
    }

    response = client.post(
        WEBHOOK_PATH,
        data=params,
        headers=_signed_headers(params),
    )

    assert response.status_code == 200
    assert parser.calls == []
    assert storage.list_orders() == []
    assert fake_service.calls == []

    # The duplicate is only detected after the claim is acquired, so it
    # still does one acquire/release round-trip without advancing.
    assert len(claim_store.acquire_calls) == 1
    assert len(claim_store.release_calls) == 1
    assert claim_store.acquire_calls[0] == claim_store.release_calls[0]
    assert claim_store.held == {}


def test_twilio_webhook_duplicate_message_sid_each_request_does_own_claim_round_trip(
    tmp_path: Path,
) -> None:
    storage = _postgres_storage(tmp_path)
    product = _seed_product(storage)
    raw_message = "Buenas, una bandeja paisa"
    claim_store = FakeConversationCustomerClaimStore()

    parser = MockParser(result=_parse_result_for_product(product, raw_message))
    app = _create_app(
        app_settings=_settings(),
        storage=storage,
        parser=parser,
        processed_message_store=_processed_message_store(tmp_path),
        conversation_customer_claim_store=claim_store,
    )
    client = TestClient(app)

    params = {
        "MessageSid": "SM_DUPLICATE_CLAIM_ORDER",
        "From": "whatsapp:+573001112233",
        "Body": raw_message,
    }
    headers = _signed_headers(params)

    first = client.post(WEBHOOK_PATH, data=params, headers=headers)
    second = client.post(WEBHOOK_PATH, data=params, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(storage.list_orders()) == 1
    assert len(parser.calls) == 1

    # Each request acquires and releases its own claim - including the
    # second, genuine-duplicate request, which does not advance.
    assert len(claim_store.acquire_calls) == 2
    assert len(claim_store.release_calls) == 2
    assert claim_store.acquire_calls[0] == claim_store.release_calls[0]
    assert claim_store.acquire_calls[1] == claim_store.release_calls[1]

    first_holder = claim_store.acquire_calls[0][2]
    second_holder = claim_store.acquire_calls[1][2]
    assert first_holder != second_holder

    tenant_id, customer_key, _ = claim_store.acquire_calls[0]
    assert claim_store.acquire_calls[1][:2] == (tenant_id, customer_key)
    assert claim_store.held == {}


def test_twilio_webhook_advance_renew_callback_invokes_claim_store_renew(
    tmp_path: Path,
) -> None:
    storage = InMemoryStorage()
    fake_service = FakeConversationAdvancementService()
    processed_store = _processed_message_store(tmp_path)
    claim_store = FakeConversationCustomerClaimStore()
    app = _create_app(
        app_settings=_settings(),
        storage=storage,
        processed_message_store=processed_store,
        conversation_advancement_service=fake_service,
        conversation_customer_claim_store=claim_store,
    )
    client = TestClient(app)

    params = {
        "MessageSid": "SM_RENEW_WIRING",
        "From": "whatsapp:+573001112233",
        "Body": "Buenas, una bandeja paisa",
    }

    response = client.post(
        WEBHOOK_PATH,
        data=params,
        headers=_signed_headers(params),
    )

    assert response.status_code == 200
    assert len(fake_service.calls) == 1

    renew_customer_claim = fake_service.calls[0].renew_customer_claim
    assert renew_customer_claim is not None

    tenant_id, customer_key, holder_id = claim_store.acquire_calls[0]

    # The webhook's claim has already been released by this point, so the
    # renew closure correctly reports the lease as no longer held - but it
    # must still call through to the claim store with the right identity.
    assert renew_customer_claim() is False
    assert claim_store.renew_calls == [(tenant_id, customer_key, holder_id)]


def test_process_validated_inbound_message_outcome_mapping(tmp_path: Path) -> None:
    storage = InMemoryStorage()
    fake_service = FakeConversationAdvancementService()
    processed_store = _processed_message_store(tmp_path)
    claim_store = FakeConversationCustomerClaimStore()
    app = _create_app(
        app_settings=_settings(),
        storage=storage,
        processed_message_store=processed_store,
        conversation_advancement_service=fake_service,
        conversation_customer_claim_store=claim_store,
    )

    tenant_id = DEFAULT_TEST_TENANT_ID
    customer_phone = "+573001112233"
    raw_sender = "whatsapp:+573001112233"
    customer_key = normalize_customer_claim_key(tenant_id, customer_phone)

    # CLAIM_BUSY: another holder already holds the conversation claim, so the
    # message is neither recorded nor advanced.
    assert claim_store.try_acquire(
        tenant_id=tenant_id,
        customer_key=customer_key,
        holder_id="other-holder",
    )

    busy_result = _process_validated_inbound_message(
        app=app,
        tenant_id=tenant_id,
        message_sid="SM_HELPER_BUSY",
        raw_sender=raw_sender,
        customer_phone=customer_phone,
        inbound_body="Buenas, una bandeja paisa",
        received_at=utc_now(),
    )

    assert busy_result.outcome is ValidatedInboundProcessingOutcome.CLAIM_BUSY
    assert processed_store.get_message("SM_HELPER_BUSY") is None
    assert fake_service.calls == []
    assert claim_store.held[(tenant_id, customer_key)] == "other-holder"

    claim_store.release(
        tenant_id=tenant_id,
        customer_key=customer_key,
        holder_id="other-holder",
    )

    # PROCESSED: a brand-new message with a non-empty body advances once.
    processed_result = _process_validated_inbound_message(
        app=app,
        tenant_id=tenant_id,
        message_sid="SM_HELPER_NEW",
        raw_sender=raw_sender,
        customer_phone=customer_phone,
        inbound_body="Buenas, una bandeja paisa",
        received_at=utc_now(),
    )

    assert processed_result.outcome is ValidatedInboundProcessingOutcome.PROCESSED
    assert len(fake_service.calls) == 1
    assert fake_service.calls[0].from_number == customer_phone

    # DUPLICATE: replaying the same MessageSid does not advance again.
    duplicate_result = _process_validated_inbound_message(
        app=app,
        tenant_id=tenant_id,
        message_sid="SM_HELPER_NEW",
        raw_sender=raw_sender,
        customer_phone=customer_phone,
        inbound_body="Buenas, una bandeja paisa",
        received_at=utc_now(),
    )

    assert duplicate_result.outcome is ValidatedInboundProcessingOutcome.DUPLICATE
    assert len(fake_service.calls) == 1
    assert claim_store.held == {}
