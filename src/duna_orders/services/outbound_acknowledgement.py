from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from duna_orders.domain.models import Order
from duna_orders.services.acknowledgement_template import (
    generate_order_confirmed_acknowledgement,
)
from duna_orders.storage.outbound_messages import (
    ORDER_CONFIRMED_ACK,
    ClaimReason,
    OutboundAcknowledgement,
    OutboundAcknowledgementStore,
)


ProviderOutcome = Literal["success", "failed", "unknown"]
ServiceOutcome = Literal["sent", "failed", "unknown", "suppressed"]


class TenantScopedOrderReader(Protocol):
    def get_order(
        self,
        *,
        tenant_id: str,
        order_id: str,
    ) -> Order | None:
        ...


@dataclass(frozen=True)
class OutboundProviderResult:
    outcome: ProviderOutcome
    provider_message_sid: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    @classmethod
    def success(cls, *, provider_message_sid: str) -> OutboundProviderResult:
        _require_text(provider_message_sid, "provider_message_sid")
        return cls(outcome="success", provider_message_sid=provider_message_sid)

    @classmethod
    def failed(
        cls,
        *,
        error_code: str | None,
        error_message: str,
    ) -> OutboundProviderResult:
        _require_text(error_message, "error_message")
        return cls(
            outcome="failed",
            error_code=error_code,
            error_message=error_message,
        )

    @classmethod
    def unknown(
        cls,
        *,
        error_code: str | None,
        error_message: str,
    ) -> OutboundProviderResult:
        _require_text(error_message, "error_message")
        return cls(
            outcome="unknown",
            error_code=error_code,
            error_message=error_message,
        )


class OutboundMessageAdapter(Protocol):
    def send_message(
        self,
        *,
        from_number: str,
        to_number: str,
        body: str,
    ) -> OutboundProviderResult:
        ...


@dataclass(frozen=True)
class OutboundAcknowledgementResult:
    acknowledgement: OutboundAcknowledgement
    outcome: ServiceOutcome
    attempted: bool
    sent: bool
    reason: ClaimReason | ProviderOutcome
    provider_result: OutboundProviderResult | None = None


class OutboundAcknowledgementService:
    def __init__(
        self,
        *,
        order_reader: TenantScopedOrderReader,
        store: OutboundAcknowledgementStore,
        adapter: OutboundMessageAdapter,
    ) -> None:
        self._order_reader = order_reader
        self._store = store
        self._adapter = adapter

    def send_order_confirmed_acknowledgement(
        self,
        *,
        tenant_id: str,
        order_id: str,
        from_number: str,
        requested_by: str,
        business_name: str | None = None,
        retry_failed: bool = False,
    ) -> OutboundAcknowledgementResult:
        _require_text(tenant_id, "tenant_id")
        _require_text(order_id, "order_id")
        _require_text(from_number, "from_number")
        _require_text(requested_by, "requested_by")

        order = self._order_reader.get_order(
            tenant_id=tenant_id,
            order_id=order_id,
        )

        if order is None:
            raise ValueError(f"Order not found for tenant: {order_id}")

        if order.status != "confirmed":
            raise ValueError(
                "Only confirmed orders can be acknowledged; "
                f"current status is {order.status}"
            )

        to_number = _require_order_customer_phone(order)
        body = generate_order_confirmed_acknowledgement(
            order,
            business_name=business_name,
        )
        claim = self._store.claim_order_acknowledgement_for_send(
            tenant_id=tenant_id,
            order_id=order_id,
            acknowledgement_type=ORDER_CONFIRMED_ACK,
            to_number=to_number,
            from_number=from_number,
            body=body,
            requested_by=requested_by,
            retry_failed=retry_failed,
        )

        if not claim.claimed_for_send:
            return OutboundAcknowledgementResult(
                acknowledgement=claim.acknowledgement,
                outcome="suppressed",
                attempted=False,
                sent=False,
                reason=claim.reason,
            )

        provider_result = self._adapter.send_message(
            from_number=from_number,
            to_number=to_number,
            body=body,
        )

        if provider_result.outcome == "success":
            if provider_result.provider_message_sid is None:
                raise ValueError("provider_message_sid is required for success")

            acknowledgement = self._store.mark_sent(
                outbound_message_id=claim.acknowledgement.outbound_message_id,
                provider_message_sid=provider_result.provider_message_sid,
            )
            return OutboundAcknowledgementResult(
                acknowledgement=acknowledgement,
                outcome="sent",
                attempted=True,
                sent=True,
                reason="success",
                provider_result=provider_result,
            )

        if provider_result.outcome == "failed":
            acknowledgement = self._store.mark_failed(
                outbound_message_id=claim.acknowledgement.outbound_message_id,
                error_code=provider_result.error_code,
                error_message=provider_result.error_message
                or "Provider rejected outbound acknowledgement",
            )
            return OutboundAcknowledgementResult(
                acknowledgement=acknowledgement,
                outcome="failed",
                attempted=True,
                sent=False,
                reason="failed",
                provider_result=provider_result,
            )

        acknowledgement = self._store.mark_unknown(
            outbound_message_id=claim.acknowledgement.outbound_message_id,
            error_code=provider_result.error_code,
            error_message=provider_result.error_message
            or "Provider outcome is unknown",
        )
        return OutboundAcknowledgementResult(
            acknowledgement=acknowledgement,
            outcome="unknown",
            attempted=True,
            sent=False,
            reason="unknown",
            provider_result=provider_result,
        )


def _require_order_customer_phone(order: Order) -> str:
    phone = order.customer_phone_snapshot

    if phone is None or not phone.strip():
        raise ValueError("customer_phone_snapshot is required")

    return phone


def _require_text(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name} is required")
