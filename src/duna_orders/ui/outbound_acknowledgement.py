from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from duna_orders.services.outbound_acknowledgement import (
    OutboundAcknowledgementOutcome,
    OutboundAcknowledgementResult,
)


OutboundAcknowledgementUiSeverity = Literal["success", "info", "warning", "error"]

_BLOCKED_PRECONDITION_FALLBACK_MESSAGE = "Acknowledgement cannot be sent yet."


@dataclass(frozen=True)
class OutboundAcknowledgementUiMessage:
    severity: OutboundAcknowledgementUiSeverity
    message: str


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
