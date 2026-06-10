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
        return OutboundProviderResult.success(provider_message_id="SM_UI_SENT")


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

    _send_button(app).click().run()

    assert app.exception == []
    assert _info_values(app) == ["Acknowledgement was already sent."]
    assert "Send acknowledgement" not in _button_labels(app)
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


def _button_labels(app: AppTest) -> list[str]:
    return [button.label for button in app.button]


def _info_values(app: AppTest) -> list[str]:
    return [info.value for info in app.info]


def _send_button(app: AppTest):
    matches = [button for button in app.button if button.label == "Send acknowledgement"]
    assert len(matches) == 1
    return matches[0]
