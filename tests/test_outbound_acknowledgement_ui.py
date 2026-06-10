from __future__ import annotations

from types import SimpleNamespace

import pytest

from duna_orders.services.outbound_acknowledgement import (
    OutboundAcknowledgementOutcome,
    OutboundAcknowledgementResult,
)
from duna_orders.ui.outbound_acknowledgement import (
    map_acknowledgement_result_to_ui_message,
)


@pytest.mark.parametrize(
    ("outcome", "expected_severity", "expected_message"),
    [
        (
            OutboundAcknowledgementOutcome.SENT,
            "success",
            "Acknowledgement sent.",
        ),
        (
            OutboundAcknowledgementOutcome.SUPPRESSED_DUPLICATE,
            "info",
            "Acknowledgement was already sent.",
        ),
        (
            OutboundAcknowledgementOutcome.FAILED_RETRYABLE,
            "warning",
            "The message was not sent. You can retry.",
        ),
        (
            OutboundAcknowledgementOutcome.MAY_HAVE_SENT_INVESTIGATE,
            "warning",
            "The message may have been sent. Verify before retrying.",
        ),
    ],
)
def test_acknowledgement_outcome_maps_to_safe_ui_message(
    outcome: OutboundAcknowledgementOutcome,
    expected_severity: str,
    expected_message: str,
) -> None:
    result = OutboundAcknowledgementResult(
        outcome=outcome,
        reason="raw provider detail should not be used",
        attempted=True,
        sent=outcome == OutboundAcknowledgementOutcome.SENT,
    )

    message = map_acknowledgement_result_to_ui_message(result)

    assert message.severity == expected_severity
    assert message.message == expected_message


def test_blocked_precondition_uses_safe_service_reason() -> None:
    result = OutboundAcknowledgementResult(
        outcome=OutboundAcknowledgementOutcome.BLOCKED_PRECONDITION,
        reason="Customer phone number is required.",
        attempted=False,
        sent=False,
    )

    message = map_acknowledgement_result_to_ui_message(result)

    assert message.severity == "info"
    assert message.message == "Customer phone number is required."


@pytest.mark.parametrize("reason", ["", "   ", None])
def test_blocked_precondition_without_reason_uses_generic_safe_message(
    reason: object,
) -> None:
    result = SimpleNamespace(
        outcome=OutboundAcknowledgementOutcome.BLOCKED_PRECONDITION,
        reason=reason,
        attempted=False,
        sent=False,
    )

    message = map_acknowledgement_result_to_ui_message(result)

    assert message.severity == "info"
    assert message.message == "Acknowledgement cannot be sent yet."


def test_provider_message_id_is_not_leaked() -> None:
    result = SimpleNamespace(
        outcome=OutboundAcknowledgementOutcome.SENT,
        reason="provider_message_id=SM123",
        attempted=True,
        sent=True,
        provider_message_id="SM123",
    )

    message = map_acknowledgement_result_to_ui_message(result)

    assert message.message == "Acknowledgement sent."
    assert "SM123" not in message.message
    assert "provider_message_id" not in message.message


def test_provider_error_details_are_not_leaked() -> None:
    result = SimpleNamespace(
        outcome=OutboundAcknowledgementOutcome.FAILED_RETRYABLE,
        reason="Twilio error 21910 invalid From/To pair",
        attempted=True,
        sent=False,
        error_code="21910",
        error_message="invalid From/To pair",
    )

    message = map_acknowledgement_result_to_ui_message(result)

    assert message.message == "The message was not sent. You can retry."
    assert "21910" not in message.message
    assert "invalid From/To pair" not in message.message
    assert "Twilio" not in message.message
