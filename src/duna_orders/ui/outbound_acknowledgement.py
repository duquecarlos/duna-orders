from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from duna_orders.services.outbound_acknowledgement import (
    OutboundAcknowledgementOutcome,
    OutboundAcknowledgementResult,
)
from duna_orders.storage.outbound_messages import OutboundAcknowledgement


OutboundAcknowledgementUiSeverity = Literal["success", "info", "warning", "error"]

_BLOCKED_PRECONDITION_FALLBACK_MESSAGE = "Acknowledgement cannot be sent yet."
_OUTBOUND_DISABLED_MESSAGE = "Outbound acknowledgement is disabled."
_OUTBOUND_NOT_CONFIGURED_MESSAGE = "Outbound acknowledgement is not fully configured."


@dataclass(frozen=True)
class OutboundAcknowledgementUiMessage:
    severity: OutboundAcknowledgementUiSeverity
    message: str


@dataclass(frozen=True)
class OutboundAcknowledgementStatusUiState:
    message: str
    show_send_button: bool
    show_retry_button: bool = False


def map_acknowledgement_result_to_ui_message(
    result: OutboundAcknowledgementResult,
) -> OutboundAcknowledgementUiMessage:
    if result.outcome == OutboundAcknowledgementOutcome.SENT:
        return OutboundAcknowledgementUiMessage(
            severity="success",
            message="Acknowledgement sent.",
        )

    if result.outcome == OutboundAcknowledgementOutcome.SUPPRESSED_DUPLICATE:
        return OutboundAcknowledgementUiMessage(
            severity="info",
            message="Acknowledgement was already sent.",
        )

    if result.outcome == OutboundAcknowledgementOutcome.FAILED_RETRYABLE:
        return OutboundAcknowledgementUiMessage(
            severity="warning",
            message="The message was not sent. You can retry.",
        )

    if result.outcome == OutboundAcknowledgementOutcome.MAY_HAVE_SENT_INVESTIGATE:
        return OutboundAcknowledgementUiMessage(
            severity="warning",
            message="The message may have been sent. Verify before retrying.",
        )

    if result.outcome == OutboundAcknowledgementOutcome.BLOCKED_PRECONDITION:
        return OutboundAcknowledgementUiMessage(
            severity="info",
            message=_blocked_precondition_message(result.reason),
        )

    return OutboundAcknowledgementUiMessage(
        severity="warning",
        message=_BLOCKED_PRECONDITION_FALLBACK_MESSAGE,
    )


def _blocked_precondition_message(reason: object) -> str:
    if isinstance(reason, str) and reason.strip():
        return reason

    return _BLOCKED_PRECONDITION_FALLBACK_MESSAGE


def map_acknowledgement_unavailable_reason_to_ui_message(
    reason: str | None,
) -> str:
    if reason == _OUTBOUND_DISABLED_MESSAGE:
        return _OUTBOUND_DISABLED_MESSAGE

    return _OUTBOUND_NOT_CONFIGURED_MESSAGE


def map_acknowledgement_status_to_ui_state(
    acknowledgement: OutboundAcknowledgement | None,
    *,
    has_required_order_details: bool,
) -> OutboundAcknowledgementStatusUiState:
    if not has_required_order_details:
        return OutboundAcknowledgementStatusUiState(
            message="Acknowledgement cannot be sent — order is missing required details.",
            show_send_button=False,
        )

    if acknowledgement is None:
        return OutboundAcknowledgementStatusUiState(
            message="No acknowledgement has been sent yet.",
            show_send_button=True,
        )

    if acknowledgement.status == "sent":
        return OutboundAcknowledgementStatusUiState(
            message="Acknowledgement was already sent.",
            show_send_button=False,
        )

    if acknowledgement.status in {"send_requested", "sending"}:
        return OutboundAcknowledgementStatusUiState(
            message="Acknowledgement is being sent.",
            show_send_button=False,
        )

    if acknowledgement.status == "unknown":
        return OutboundAcknowledgementStatusUiState(
            message=(
                "Acknowledgement status is unclear — it may already have been sent. "
                "Check before taking any action."
            ),
            show_send_button=False,
        )

    if acknowledgement.status == "failed":
        return OutboundAcknowledgementStatusUiState(
            message="Acknowledgement was not sent. You can retry.",
            show_send_button=False,
            show_retry_button=True,
        )

    return OutboundAcknowledgementStatusUiState(
        message=(
            "Acknowledgement status is unclear — it may already have been sent. "
            "Check before taking any action."
        ),
        show_send_button=False,
    )
