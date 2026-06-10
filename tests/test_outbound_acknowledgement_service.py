from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import pytest

from duna_orders.domain.models import Order, OrderItem, OrderStatus
from duna_orders.services.acknowledgement_template import (
    generate_order_confirmed_acknowledgement,
)
from duna_orders.services.outbound_acknowledgement import (
    OutboundAcknowledgementService,
    OutboundProviderResult,
)
from duna_orders.storage.outbound_messages import (
    ORDER_CONFIRMED_ACK,
    PostgresOutboundAcknowledgementStore,
)
from duna_orders.storage.postgres_base import Base
from duna_orders.storage.postgres_session import make_engine, make_session_factory
from tests.conftest import DEFAULT_TEST_TENANT_ID


ORDER_ID = "ord_ack_service"
FROM_NUMBER = "whatsapp:+15551234567"
REQUESTED_BY = "operator"


@dataclass
class FakeTenantScopedOrderReader:
    orders: list[Order]
    calls: list[dict[str, str]] = field(default_factory=list)

    def get_order(
        self,
        *,
        tenant_id: str,
        order_id: str,
    ) -> Order | None:
        self.calls.append({"tenant_id": tenant_id, "order_id": order_id})
        return next(
            (
                order
                for order in self.orders
                if order.tenant_id == tenant_id and order.order_id == order_id
            ),
            None,
        )


@dataclass
class FakeOutboundAdapter:
    result: OutboundProviderResult
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


def _store(tmp_path: Path) -> PostgresOutboundAcknowledgementStore:
    database_path = tmp_path / "outbound_ack_service.db"
    engine = make_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    return PostgresOutboundAcknowledgementStore(make_session_factory(engine))


def _service(
    tmp_path: Path,
    *,
    order: Order | None = None,
    adapter_result: OutboundProviderResult | None = None,
) -> tuple[
    OutboundAcknowledgementService,
    PostgresOutboundAcknowledgementStore,
    FakeOutboundAdapter,
]:
    store = _store(tmp_path)
    adapter = FakeOutboundAdapter(
        result=adapter_result
        or OutboundProviderResult.success(provider_message_id="SM_SENT")
    )
    reader = FakeTenantScopedOrderReader([order or _order()])
    service = OutboundAcknowledgementService(
        order_reader=reader,
        store=store,
        adapter=adapter,
    )

    return service, store, adapter


def _send(
    service: OutboundAcknowledgementService,
    *,
    retry_failed: bool = False,
):
    return service.send_order_confirmed_acknowledgement(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        order_id=ORDER_ID,
        from_number=FROM_NUMBER,
        requested_by=REQUESTED_BY,
        business_name="El Fogon",
        retry_failed=retry_failed,
    )


def test_happy_path_builds_body_claims_sends_and_marks_sent(tmp_path: Path) -> None:
    order = _order()
    service, store, adapter = _service(tmp_path, order=order)

    result = _send(service)
    persisted = store.get_for_order_acknowledgement(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        order_id=ORDER_ID,
        acknowledgement_type=ORDER_CONFIRMED_ACK,
    )

    assert result.outcome == "sent"
    assert result.attempted is True
    assert result.sent is True
    assert result.acknowledgement.status == "sent"
    assert result.acknowledgement.provider_message_id == "SM_SENT"
    assert persisted == result.acknowledgement
    assert len(adapter.calls) == 1
    assert adapter.calls[0] == {
        "from_number": FROM_NUMBER,
        "to_number": order.customer_phone_snapshot,
        "body": generate_order_confirmed_acknowledgement(
            order,
            business_name="El Fogon",
        ),
    }


def test_order_read_is_tenant_scoped(tmp_path: Path) -> None:
    service, _, adapter = _service(tmp_path)

    _send(service)

    assert service._order_reader.calls == [
        {"tenant_id": DEFAULT_TEST_TENANT_ID, "order_id": ORDER_ID}
    ]
    assert len(adapter.calls) == 1


def test_duplicate_second_call_is_suppressed_without_adapter_call(tmp_path: Path) -> None:
    service, _, adapter = _service(tmp_path)
    first = _send(service)

    second = _send(service)

    assert first.outcome == "sent"
    assert second.outcome == "suppressed"
    assert second.reason == "suppressed_sent"
    assert second.attempted is False
    assert len(adapter.calls) == 1


def test_existing_sent_row_suppresses_adapter_call(tmp_path: Path) -> None:
    service, _, adapter = _service(tmp_path)
    _send(service)
    adapter.calls.clear()

    result = _send(service)

    assert result.outcome == "suppressed"
    assert result.reason == "suppressed_sent"
    assert adapter.calls == []


def test_existing_sending_row_suppresses_adapter_call(tmp_path: Path) -> None:
    service, store, adapter = _service(tmp_path)
    order = _order()
    store.claim_order_acknowledgement_for_send(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        order_id=ORDER_ID,
        acknowledgement_type=ORDER_CONFIRMED_ACK,
        to_number=order.customer_phone_snapshot or "",
        from_number=FROM_NUMBER,
        body=generate_order_confirmed_acknowledgement(order, business_name="El Fogon"),
        requested_by=REQUESTED_BY,
    )

    result = _send(service)

    assert result.outcome == "suppressed"
    assert result.reason == "suppressed_in_progress"
    assert adapter.calls == []


def test_retry_failed_does_not_send_existing_sending_row(tmp_path: Path) -> None:
    service, store, adapter = _service(tmp_path)
    order = _order()
    claim = store.claim_order_acknowledgement_for_send(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        order_id=ORDER_ID,
        acknowledgement_type=ORDER_CONFIRMED_ACK,
        to_number=order.customer_phone_snapshot or "",
        from_number=FROM_NUMBER,
        body=generate_order_confirmed_acknowledgement(order, business_name="El Fogon"),
        requested_by=REQUESTED_BY,
    )

    result = _send(service, retry_failed=True)

    assert claim.acknowledgement.status == "sending"
    assert result.outcome == "suppressed"
    assert result.reason == "suppressed_in_progress"
    assert result.acknowledgement.status == "sending"
    assert result.acknowledgement.outbound_message_id == claim.acknowledgement.outbound_message_id
    assert adapter.calls == []


def test_existing_unknown_row_suppresses_adapter_call(tmp_path: Path) -> None:
    service, _, adapter = _service(
        tmp_path,
        adapter_result=OutboundProviderResult.unknown(
            error_code="timeout",
            error_message="provider timeout",
        ),
    )
    first = _send(service)
    adapter.calls.clear()

    second = _send(service, retry_failed=True)

    assert first.outcome == "unknown"
    assert second.outcome == "suppressed"
    assert second.reason == "suppressed_unknown"
    assert adapter.calls == []


def test_failed_row_without_retry_suppresses_adapter_call(tmp_path: Path) -> None:
    service, _, adapter = _service(
        tmp_path,
        adapter_result=OutboundProviderResult.failed(
            error_code="provider_error",
            error_message="provider rejected message",
        ),
    )
    first = _send(service)
    adapter.calls.clear()

    second = _send(service)

    assert first.outcome == "failed"
    assert second.outcome == "suppressed"
    assert second.reason == "suppressed_failed_without_retry"
    assert adapter.calls == []


def test_failed_row_with_retry_calls_adapter_once_and_reuses_row(tmp_path: Path) -> None:
    service, _, adapter = _service(
        tmp_path,
        adapter_result=OutboundProviderResult.failed(
            error_code="provider_error",
            error_message="provider rejected message",
        ),
    )
    first = _send(service)
    first_id = first.acknowledgement.outbound_message_id
    adapter.result = OutboundProviderResult.success(provider_message_id="SM_RETRY")
    adapter.calls.clear()

    retry = _send(service, retry_failed=True)

    assert retry.outcome == "sent"
    assert retry.acknowledgement.outbound_message_id == first_id
    assert retry.acknowledgement.attempt_count == 2
    assert retry.acknowledgement.provider_message_id == "SM_RETRY"
    assert len(adapter.calls) == 1


def test_adapter_known_failure_marks_failed(tmp_path: Path) -> None:
    service, _, adapter = _service(
        tmp_path,
        adapter_result=OutboundProviderResult.failed(
            error_code="provider_error",
            error_message="provider rejected message",
        ),
    )

    result = _send(service)

    assert result.outcome == "failed"
    assert result.attempted is True
    assert result.acknowledgement.status == "failed"
    assert result.acknowledgement.last_error_code == "provider_error"
    assert result.acknowledgement.last_error_message == "provider rejected message"
    assert len(adapter.calls) == 1


def test_adapter_unknown_marks_unknown(tmp_path: Path) -> None:
    service, _, adapter = _service(
        tmp_path,
        adapter_result=OutboundProviderResult.unknown(
            error_code="timeout",
            error_message="provider response unknown",
        ),
    )

    result = _send(service)

    assert result.outcome == "unknown"
    assert result.attempted is True
    assert result.acknowledgement.status == "unknown"
    assert result.acknowledgement.last_error_code == "timeout"
    assert result.acknowledgement.last_error_message == "provider response unknown"
    assert len(adapter.calls) == 1


@pytest.mark.parametrize("status", ["draft", "approved", "cancelled"])
def test_only_confirmed_orders_can_be_acknowledged(
    tmp_path: Path,
    status: OrderStatus,
) -> None:
    service, store, adapter = _service(tmp_path, order=_order(status=status))

    with pytest.raises(ValueError, match="Only confirmed orders"):
        _send(service)

    assert adapter.calls == []
    assert _stored_ack(store) is None


def test_cross_tenant_order_is_not_read_or_claimed(tmp_path: Path) -> None:
    service, store, adapter = _service(tmp_path, order=_order(tenant_id="tenant-b"))

    with pytest.raises(ValueError, match="Order not found for tenant"):
        _send(service)

    assert service._order_reader.calls == [
        {"tenant_id": DEFAULT_TEST_TENANT_ID, "order_id": ORDER_ID}
    ]
    assert adapter.calls == []
    assert _stored_ack(store) is None


def test_missing_order_is_blocked_before_claim_or_send(tmp_path: Path) -> None:
    service, store, adapter = _service(tmp_path)

    with pytest.raises(ValueError, match="Order not found for tenant"):
        service.send_order_confirmed_acknowledgement(
            tenant_id=DEFAULT_TEST_TENANT_ID,
            order_id="missing-order",
            from_number=FROM_NUMBER,
            requested_by=REQUESTED_BY,
        )

    assert adapter.calls == []
    assert _stored_ack(store) is None


def test_missing_customer_phone_is_blocked_before_claim_or_send(tmp_path: Path) -> None:
    service, store, adapter = _service(
        tmp_path,
        order=_order(customer_phone_snapshot=None),
    )

    with pytest.raises(ValueError, match="customer_phone_snapshot is required"):
        _send(service)

    assert adapter.calls == []
    assert _stored_ack(store) is None


@pytest.mark.parametrize(
    ("field_name", "overrides"),
    [
        ("from_number", {"from_number": ""}),
        ("requested_by", {"requested_by": ""}),
        ("tenant_id", {"tenant_id": ""}),
        ("order_id", {"order_id": ""}),
    ],
)
def test_required_request_fields_are_validated_before_claim_or_send(
    tmp_path: Path,
    field_name: str,
    overrides: dict[str, str],
) -> None:
    service, store, adapter = _service(tmp_path)
    params = {
        "tenant_id": DEFAULT_TEST_TENANT_ID,
        "order_id": ORDER_ID,
        "from_number": FROM_NUMBER,
        "requested_by": REQUESTED_BY,
    }
    params.update(overrides)

    with pytest.raises(ValueError, match=f"{field_name} is required"):
        service.send_order_confirmed_acknowledgement(**params)

    assert adapter.calls == []
    assert _stored_ack(store) is None


def test_body_uses_deterministic_template_builder_output(tmp_path: Path) -> None:
    order = _order()
    service, _, adapter = _service(tmp_path, order=order)

    _send(service)

    assert adapter.calls[0]["body"] == generate_order_confirmed_acknowledgement(
        order,
        business_name="El Fogon",
    )


def test_order_confirmation_path_has_no_outbound_acknowledgement_dependency() -> None:
    orders_source = Path("src/duna_orders/services/orders.py").read_text()
    confirmation_source = Path("src/duna_orders/storage/order_confirmation.py").read_text()

    assert "outbound_acknowledgement" not in orders_source
    assert "OutboundAcknowledgementService" not in orders_source
    assert "OutboundMessageAdapter" not in orders_source
    assert "outbound_acknowledgement" not in confirmation_source
    assert "OutboundAcknowledgementService" not in confirmation_source
    assert "OutboundMessageAdapter" not in confirmation_source


def test_outbound_service_has_no_parser_or_real_twilio_dependency() -> None:
    source = Path("src/duna_orders/services/outbound_acknowledgement.py").read_text()

    assert "PROMPT_VERSION" not in source
    assert "messages.create" not in source
    assert "twilio.rest" not in source
    assert "parsing" not in source


def _stored_ack(store: PostgresOutboundAcknowledgementStore):
    return store.get_for_order_acknowledgement(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        order_id=ORDER_ID,
        acknowledgement_type=ORDER_CONFIRMED_ACK,
    )


def _order(
    *,
    tenant_id: str = DEFAULT_TEST_TENANT_ID,
    status: OrderStatus = "confirmed",
    customer_phone_snapshot: str | None = "whatsapp:+573001112233",
) -> Order:
    return Order(
        tenant_id=tenant_id,
        order_id=ORDER_ID,
        customer_name_snapshot="Carlos",
        customer_phone_snapshot=customer_phone_snapshot,
        raw_message="Pedido confirmado",
        status=status,
        items=[
            OrderItem(
                tenant_id=tenant_id,
                order_item_id="oit_ack_1",
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
