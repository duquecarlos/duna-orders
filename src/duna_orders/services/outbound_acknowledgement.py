from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol

from duna_orders.domain.models import Order
from duna_orders.services.acknowledgement_template import (
    generate_order_confirmed_acknowledgement,
)
from duna_orders.storage.outbound_messages import (
    ORDER_CONFIRMED_ACK,
    ClaimReason,
    OutboundAcknowledgementStore,
)


ProviderOutcome = Literal["success", "failed", "unknown"]


class OutboundAcknowledgementOutcome(StrEnum):
    SENT = "sent"
    SUPPRESSED_DUPLICATE = "suppressed_duplicate"
    FAILED_RETRYABLE = "failed_retryable"
    MAY_HAVE_SENT_INVESTIGATE = "may_have_sent_investigate"
    BLOCKED_PRECONDITION = "blocked_precondition"


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
    provider_message_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    @classmethod
    def success(cls, *, provider_message_id: str) -> OutboundProviderResult:
        _require_text(provider_message_id, "provider_message_id")
        return cls(outcome="success", provider_message_id=provider_message_id)

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
    outcome: OutboundAcknowledgementOutcome
    reason: str
    attempted: bool
    sent: bool


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
        if not _has_text(tenant_id):
            return _blocked("Tenant is required.")
        if not _has_text(order_id):
            return _blocked("Order is required.")
        if not _has_text(from_number):
            return _blocked("Sender phone number is required.")
        if not _has_text(requested_by):
            return _blocked("Operator identity is required.")

        order = self._order_reader.get_order(
            tenant_id=tenant_id,
            order_id=order_id,
        )

        if order is None:
            return _blocked("Order was not found for this tenant.")

        if order.status != "confirmed":
            return _blocked("Only confirmed orders can be acknowledged.")

        to_number = _order_customer_phone(order)
        if to_number is None:
            return _blocked("Customer phone number is required.")

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
            return _suppressed_result(claim.reason)

        provider_result = self._adapter.send_message(
            from_number=from_number,
            to_number=to_number,
            body=body,
        )

        if provider_result.outcome == "success":
            if provider_result.provider_message_id is None:
                raise ValueError("provider_message_id is required for success")

            self._store.mark_sent(
                outbound_message_id=claim.acknowledgement.outbound_message_id,
                provider_message_id=provider_result.provider_message_id,
            )
            return OutboundAcknowledgementResult(
                outcome=OutboundAcknowledgementOutcome.SENT,
                reason="Acknowledgement sent.",
                attempted=True,
                sent=True,
            )

        if provider_result.outcome == "failed":
            self._store.mark_failed(
                outbound_message_id=claim.acknowledgement.outbound_message_id,
                error_code=provider_result.error_code,
                error_message=provider_result.error_message
                or "Provider rejected outbound acknowledgement",
            )
            return OutboundAcknowledgementResult(
                outcome=OutboundAcknowledgementOutcome.FAILED_RETRYABLE,
                reason="The message was not sent. You can retry.",
                attempted=True,
                sent=False,
            )

        self._store.mark_unknown(
            outbound_message_id=claim.acknowledgement.outbound_message_id,
            error_code=provider_result.error_code,
            error_message=provider_result.error_message
            or "Provider outcome is unknown",
        )
        return OutboundAcknowledgementResult(
            outcome=OutboundAcknowledgementOutcome.MAY_HAVE_SENT_INVESTIGATE,
            reason=(
                "The message may have been sent. Verify with the provider "
                "or customer before retrying."
            ),
            attempted=True,
            sent=False,
        )


def _blocked(reason: str) -> OutboundAcknowledgementResult:
    return OutboundAcknowledgementResult(
        outcome=OutboundAcknowledgementOutcome.BLOCKED_PRECONDITION,
        reason=reason,
        attempted=False,
        sent=False,
    )


def _suppressed_result(reason: ClaimReason) -> OutboundAcknowledgementResult:
    if reason == "suppressed_sent":
        return OutboundAcknowledgementResult(
            outcome=OutboundAcknowledgementOutcome.SUPPRESSED_DUPLICATE,
            reason="Acknowledgement was already sent.",
            attempted=False,
            sent=False,
        )

    if reason == "suppressed_failed_without_retry":
        return OutboundAcknowledgementResult(
            outcome=OutboundAcknowledgementOutcome.FAILED_RETRYABLE,
            reason="The previous attempt failed. You can retry.",
            attempted=False,
            sent=False,
        )

    if reason in {"suppressed_in_progress", "suppressed_unknown"}:
        return OutboundAcknowledgementResult(
            outcome=OutboundAcknowledgementOutcome.MAY_HAVE_SENT_INVESTIGATE,
            reason=(
                "The message may have been sent. Verify with the provider "
                "or customer before retrying."
            ),
            attempted=False,
            sent=False,
        )

    return OutboundAcknowledgementResult(
        outcome=OutboundAcknowledgementOutcome.SUPPRESSED_DUPLICATE,
        reason="A duplicate acknowledgement was suppressed.",
        attempted=False,
        sent=False,
    )


def _order_customer_phone(order: Order) -> str | None:
    phone = order.customer_phone_snapshot

    if phone is None or not phone.strip():
        return None

    return phone


def _require_text(value: str, field_name: str) -> None:
    if not _has_text(value):
        raise ValueError(f"{field_name} is required")


def _has_text(value: str) -> bool:
    return bool(value and value.strip())
