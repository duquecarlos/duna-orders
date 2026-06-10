from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from streamlit.testing.v1 import AppTest

from duna_orders.config import settings
from duna_orders.demo_catalog import load_demo_catalog
from duna_orders.domain.models import Order, OrderItem
from duna_orders.services.outbound_acknowledgement import (
    OutboundAcknowledgementService,
    OutboundProviderResult,
)
from duna_orders.services.orders import OrderService
from duna_orders.services.tenant_scoped_reads import TenantScopedReadService
from duna_orders.storage.outbound_messages import (
    ORDER_CONFIRMED_ACK,
    PostgresOutboundAcknowledgementStore,
)
from duna_orders.storage.postgres_base import Base
from duna_orders.storage.postgres_session import make_engine, make_session_factory
from duna_orders.storage.memory import InMemoryStorage
from duna_orders.ui.setup import OutboundAcknowledgementServiceSetup


TENANT_ID = "el-fogon-colombiano"
ORDER_ID = "ord_today_ack_ui"
FROM_NUMBER = "whatsapp:+15551234567"


@dataclass
class FakeOutboundAdapter:
    result: OutboundProviderResult = field(
        default_factory=lambda: OutboundProviderResult.success(
            provider_message_id="SM_UI_SENT"
        )
    )
    calls: list[dict[str, str]] = field(default_factory=list)

    def send_message(
        self,
        *,
        from_number: str,
        to_number: str,
        body: str,
    ) -> OutboundProviderResult:
        self.calls.append(
            {
                "from_number": from_number,
                "to_number": to_number,
                "body": body,
            }
        )
        return self.result


class CountingOutboundAcknowledgementStore:
    def __init__(self, store: PostgresOutboundAcknowledgementStore) -> None:
        self._store = store
        self.get_calls: list[dict[str, str]] = []

    def claim_order_acknowledgement_for_send(self, **kwargs):
        return self._store.claim_order_acknowledgement_for_send(**kwargs)

    def get_for_order_acknowledgement(self, **kwargs):
        self.get_calls.append(dict(kwargs))
        return self._store.get_for_order_acknowledgement(**kwargs)

    def mark_sent(self, **kwargs):
        return self._store.mark_sent(**kwargs)

    def mark_failed(self, **kwargs):
        return self._store.mark_failed(**kwargs)

    def mark_unknown(self, **kwargs):
        return self._store.mark_unknown(**kwargs)


def test_orders_today_acknowledgement_button_routes_through_service_and_rereads(
    tmp_path: Path,
) -> None:
    storage = _storage_with_confirmed_order()
    store = CountingOutboundAcknowledgementStore(_outbound_store(tmp_path))
    adapter = FakeOutboundAdapter()
    setup = OutboundAcknowledgementServiceSetup(
        service=OutboundAcknowledgementService(
            order_reader=TenantScopedReadService(storage),
            store=store,
            adapter=adapter,
        ),
        acknowledgement_store=store,
        tenant_id=TENANT_ID,
        from_number=FROM_NUMBER,
    )
    app = _orders_today_app(storage=storage, setup=setup)

    app.run()

    assert app.exception == []
    assert _info_values(app) == ["No acknowledgement has been sent yet."]
    assert "Send acknowledgement" in _button_labels(app)
    assert "Retry acknowledgement" not in _button_labels(app)

    _send_button(app).click().run()

    assert app.exception == []
    assert _info_values(app) == ["Acknowledgement was already sent."]
    assert "Send acknowledgement" not in _button_labels(app)
    assert "Retry acknowledgement" not in _button_labels(app)
    assert len(adapter.calls) == 1
    assert len(store.get_calls) >= 2

    stored = store.get_for_order_acknowledgement(
        tenant_id=TENANT_ID,
        order_id=ORDER_ID,
        acknowledgement_type=ORDER_CONFIRMED_ACK,
    )
    assert stored is not None
    assert stored.status == "sent"
    assert stored.attempt_count == 1

    adapter.calls.clear()
    duplicate = setup.service.send_order_confirmed_acknowledgement(
        tenant_id=TENANT_ID,
        order_id=ORDER_ID,
        from_number=FROM_NUMBER,
        requested_by="operator",
        business_name="El Fogon",
    )

    assert duplicate.reason == "Acknowledgement was already sent."
    assert adapter.calls == []


def test_orders_today_failed_acknowledgement_requires_confirmation_before_retry(
    tmp_path: Path,
) -> None:
    storage = _storage_with_confirmed_order()
    store = CountingOutboundAcknowledgementStore(_outbound_store(tmp_path))
    _seed_failed_acknowledgement(store)
    adapter = FakeOutboundAdapter()
    setup = OutboundAcknowledgementServiceSetup(
        service=OutboundAcknowledgementService(
            order_reader=TenantScopedReadService(storage),
            store=store,
            adapter=adapter,
        ),
        acknowledgement_store=store,
        tenant_id=TENANT_ID,
        from_number=FROM_NUMBER,
    )
    app = _orders_today_app(storage=storage, setup=setup)

    app.run()

    assert app.exception == []
    assert _info_values(app) == ["Acknowledgement was not sent. You can retry."]
    assert "Retry acknowledgement" in _button_labels(app)
    assert "Send acknowledgement" not in _button_labels(app)

    _retry_button(app).click().run()

    assert app.exception == []
    assert _warning_values(app) == [
        "Send this acknowledgement again? The previous attempt failed."
    ]
    assert "Confirm retry acknowledgement" in _button_labels(app)
    assert adapter.calls == []
    stored_after_retry_click = store.get_for_order_acknowledgement(
        tenant_id=TENANT_ID,
        order_id=ORDER_ID,
        acknowledgement_type=ORDER_CONFIRMED_ACK,
    )
    assert stored_after_retry_click is not None
    assert stored_after_retry_click.status == "failed"
    assert stored_after_retry_click.attempt_count == 1


def test_orders_today_confirmed_retry_routes_through_service_and_rereads(
    tmp_path: Path,
) -> None:
    storage = _storage_with_confirmed_order()
    store = CountingOutboundAcknowledgementStore(_outbound_store(tmp_path))
    failed = _seed_failed_acknowledgement(store)
    adapter = FakeOutboundAdapter()
    setup = OutboundAcknowledgementServiceSetup(
        service=OutboundAcknowledgementService(
            order_reader=TenantScopedReadService(storage),
            store=store,
            adapter=adapter,
        ),
        acknowledgement_store=store,
        tenant_id=TENANT_ID,
        from_number=FROM_NUMBER,
    )
    app = _orders_today_app(storage=storage, setup=setup)

    app.run()
    _retry_button(app).click().run()
    _confirm_retry_button(app).click().run()

    assert app.exception == []
    assert _info_values(app) == ["Acknowledgement was already sent."]
    assert "Retry acknowledgement" not in _button_labels(app)
    assert "Send acknowledgement" not in _button_labels(app)
    assert len(adapter.calls) == 1
    assert len(store.get_calls) >= 2

    stored = store.get_for_order_acknowledgement(
        tenant_id=TENANT_ID,
        order_id=ORDER_ID,
        acknowledgement_type=ORDER_CONFIRMED_ACK,
    )
    assert stored is not None
    assert stored.outbound_message_id == failed.outbound_message_id
    assert stored.status == "sent"
    assert stored.attempt_count == 2


def test_orders_today_retry_unknown_rereads_and_hides_retry(
    tmp_path: Path,
) -> None:
    storage = _storage_with_confirmed_order()
    store = CountingOutboundAcknowledgementStore(_outbound_store(tmp_path))
    _seed_failed_acknowledgement(store)
    adapter = FakeOutboundAdapter(
        result=OutboundProviderResult.unknown(
            error_code="timeout",
            error_message="provider response unknown",
        )
    )
    setup = OutboundAcknowledgementServiceSetup(
        service=OutboundAcknowledgementService(
            order_reader=TenantScopedReadService(storage),
            store=store,
            adapter=adapter,
        ),
        acknowledgement_store=store,
        tenant_id=TENANT_ID,
        from_number=FROM_NUMBER,
    )
    app = _orders_today_app(storage=storage, setup=setup)

    app.run()
    _retry_button(app).click().run()
    _confirm_retry_button(app).click().run()

    assert app.exception == []
    assert _info_values(app) == [
        "Acknowledgement status is unclear — it may already have been sent. "
        "Check before taking any action."
    ]
    assert "Retry acknowledgement" not in _button_labels(app)
    assert "Send acknowledgement" not in _button_labels(app)
    assert len(adapter.calls) == 1

    stored = store.get_for_order_acknowledgement(
        tenant_id=TENANT_ID,
        order_id=ORDER_ID,
        acknowledgement_type=ORDER_CONFIRMED_ACK,
    )
    assert stored is not None
    assert stored.status == "unknown"
    assert stored.attempt_count == 2


def test_orders_today_retry_strings_do_not_expose_provider_or_delivery_terms(
    tmp_path: Path,
) -> None:
    storage = _storage_with_confirmed_order()
    store = CountingOutboundAcknowledgementStore(_outbound_store(tmp_path))
    _seed_failed_acknowledgement(store)
    setup = OutboundAcknowledgementServiceSetup(
        service=OutboundAcknowledgementService(
            order_reader=TenantScopedReadService(storage),
            store=store,
            adapter=FakeOutboundAdapter(),
        ),
        acknowledgement_store=store,
        tenant_id=TENANT_ID,
        from_number=FROM_NUMBER,
    )
    app = _orders_today_app(storage=storage, setup=setup)

    app.run()
    _retry_button(app).click().run()

    rendered = " ".join(
        [*_info_values(app), *_warning_values(app), *_button_labels(app)]
    )
    assert "provider_message_id" not in rendered
    assert "error_code" not in rendered
    assert "Twilio" not in rendered
    assert "twilio" not in rendered
    assert "provider" not in rendered
    assert "delivered" not in rendered
    assert "received" not in rendered
    assert "notified" not in rendered
    assert "customer saw" not in rendered
    assert "confirmed received" not in rendered


def test_orders_today_acknowledgement_unavailable_behavior_is_unchanged() -> None:
    storage = _storage_with_confirmed_order()
    setup = OutboundAcknowledgementServiceSetup(
        service=None,
        unavailable_reason="Outbound acknowledgement is disabled.",
    )
    app = _orders_today_app(storage=storage, setup=setup)

    app.run()

    assert app.exception == []
    assert _info_values(app) == ["Outbound acknowledgement is disabled."]
    assert "Send acknowledgement" not in _button_labels(app)
    assert "Retry acknowledgement" not in _button_labels(app)


def test_orders_today_acknowledgement_not_ready_message_is_provider_neutral() -> None:
    storage = _storage_with_confirmed_order()
    setup = OutboundAcknowledgementServiceSetup(
        service=None,
        unavailable_reason="Twilio account SID is not configured.",
    )
    app = _orders_today_app(storage=storage, setup=setup)

    app.run()

    assert app.exception == []
    assert _info_values(app) == [
        "Outbound acknowledgement is not fully configured."
    ]
    assert "Send acknowledgement" not in _button_labels(app)
    assert "Retry acknowledgement" not in _button_labels(app)
    rendered = " ".join(_info_values(app))
    assert "Twilio" not in rendered
    assert "twilio" not in rendered
    assert "provider" not in rendered
    assert "provider_message_id" not in rendered
    assert "error_code" not in rendered
    assert "account SID" not in rendered
    assert "auth token" not in rendered
    assert "sender" not in rendered


def test_orders_today_does_not_call_outbound_adapter_directly() -> None:
    source = Path("pages/2_Orders_Today.py").read_text()

    assert "TwilioOutboundMessageAdapter" not in source
    assert ".send_message(" not in source


def _orders_today_app(
    *,
    storage: InMemoryStorage,
    setup: OutboundAcknowledgementServiceSetup,
) -> AppTest:
    catalog = load_demo_catalog()
    app = AppTest.from_file("pages/2_Orders_Today.py", default_timeout=10)
    app.session_state["demo_catalog"] = catalog
    app.session_state["storage"] = storage
    app.session_state["order_service"] = OrderService(storage)
    app.session_state["outbound_acknowledgement_setup"] = setup
    return app


def _storage_with_confirmed_order() -> InMemoryStorage:
    storage = InMemoryStorage()
    storage.create_order(_confirmed_order())
    return storage


def _confirmed_order() -> Order:
    now = datetime.now(ZoneInfo(settings.default_timezone))
    return Order(
        tenant_id=TENANT_ID,
        order_id=ORDER_ID,
        created_at=now,
        updated_at=now,
        customer_name_snapshot="UI Status Smoke",
        customer_phone_snapshot="whatsapp:+573001112233",
        raw_message="Pedido confirmado",
        status="confirmed",
        confirmed_at=now,
        status_updated_at=now,
        items=[
            OrderItem(
                tenant_id=TENANT_ID,
                order_item_id="oit_today_ack_ui",
                order_id=ORDER_ID,
                product_id="prd_bandeja",
                product_name_snapshot="Bandeja paisa",
                quantity=Decimal("1"),
                unit_price_snapshot=Decimal("58000"),
                line_total=Decimal("58000"),
                modifications="sin aguacate",
                validation_status="ok",
            )
        ],
        subtotal=Decimal("58000"),
        total=Decimal("58000"),
        fulfillment_type="delivery",
        delivery_zone="Chapinero",
        payment_method="nequi",
    )


def _outbound_store(tmp_path: Path) -> PostgresOutboundAcknowledgementStore:
    database_path = tmp_path / "orders_today_outbound_ack.db"
    engine = make_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    return PostgresOutboundAcknowledgementStore(make_session_factory(engine))


def _seed_failed_acknowledgement(
    store: CountingOutboundAcknowledgementStore,
):
    order = _confirmed_order()
    claim = store.claim_order_acknowledgement_for_send(
        tenant_id=TENANT_ID,
        order_id=ORDER_ID,
        acknowledgement_type=ORDER_CONFIRMED_ACK,
        to_number=order.customer_phone_snapshot or "",
        from_number=FROM_NUMBER,
        body="Hola, tu pedido quedo confirmado.",
        requested_by="operator",
    )
    return store.mark_failed(
        outbound_message_id=claim.acknowledgement.outbound_message_id,
        error_code="provider_error",
        error_message="provider rejected message",
    )


def _button_labels(app: AppTest) -> list[str]:
    return [button.label for button in app.button]


def _info_values(app: AppTest) -> list[str]:
    return [info.value for info in app.info]


def _warning_values(app: AppTest) -> list[str]:
    return [warning.value for warning in app.warning]


def _send_button(app: AppTest):
    matches = [button for button in app.button if button.label == "Send acknowledgement"]
    assert len(matches) == 1
    return matches[0]


def _retry_button(app: AppTest):
    matches = [button for button in app.button if button.label == "Retry acknowledgement"]
    assert len(matches) == 1
    return matches[0]


def _confirm_retry_button(app: AppTest):
    matches = [
        button
        for button in app.button
        if button.label == "Confirm retry acknowledgement"
    ]
    assert len(matches) == 1
    return matches[0]
