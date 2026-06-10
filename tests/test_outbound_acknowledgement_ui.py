from __future__ import annotations

from types import SimpleNamespace

import pytest

from duna_orders.services.outbound_acknowledgement import (
    OutboundAcknowledgementOutcome,
    OutboundAcknowledgementResult,
)
from duna_orders.ui.outbound_acknowledgement import (
    map_acknowledgement_status_to_ui_state,
    map_acknowledgement_result_to_ui_message,
    map_acknowledgement_unavailable_reason_to_ui_message,
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


@pytest.mark.parametrize(
    ("status", "expected_message", "expected_show_send_button"),
    [
        (None, "No acknowledgement has been sent yet.", True),
        ("sent", "Acknowledgement was already sent.", False),
        ("sending", "Acknowledgement is being sent.", False),
        ("send_requested", "Acknowledgement is being sent.", False),
        (
            "unknown",
            (
                "Acknowledgement status is unclear — it may already have been sent. "
                "Check before taking any action."
            ),
            False,
        ),
        (
            "failed",
            "Acknowledgement could not be sent. Retry is not available yet.",
            False,
        ),
    ],
)
def test_acknowledgement_status_maps_to_display_state(
    status: str | None,
    expected_message: str,
    expected_show_send_button: bool,
) -> None:
    acknowledgement = None
    if status is not None:
        acknowledgement = SimpleNamespace(status=status)

    state = map_acknowledgement_status_to_ui_state(
        acknowledgement,
        has_required_order_details=True,
    )

    assert state.message == expected_message
    assert state.show_send_button is expected_show_send_button


def test_blocked_precondition_status_hides_button() -> None:
    state = map_acknowledgement_status_to_ui_state(
        None,
        has_required_order_details=False,
    )

    assert (
        state.message
        == "Acknowledgement cannot be sent — order is missing required details."
    )
    assert state.show_send_button is False


def test_unknown_status_hides_button_customer_harm_gate() -> None:
    state = map_acknowledgement_status_to_ui_state(
        SimpleNamespace(status="unknown"),
        has_required_order_details=True,
    )

    assert state.show_send_button is False


def test_sending_status_hides_button_customer_harm_gate() -> None:
    state = map_acknowledgement_status_to_ui_state(
        SimpleNamespace(status="sending"),
        has_required_order_details=True,
    )

    assert state.show_send_button is False


def test_status_display_does_not_leak_provider_details() -> None:
    acknowledgement = SimpleNamespace(
        status="unknown",
        provider_message_id="SM123",
        last_error_code="21910",
        last_error_message="Twilio provider failure",
        provider="twilio",
    )

    state = map_acknowledgement_status_to_ui_state(
        acknowledgement,
        has_required_order_details=True,
    )

    rendered = f"{state.message} {state.show_send_button}"
    assert "provider_message_id" not in rendered
    assert "SM123" not in rendered
    assert "21910" not in rendered
    assert "Twilio" not in rendered
    assert "provider" not in rendered
    assert "twilio" not in rendered


def test_disabled_unavailable_reason_remains_operator_visible() -> None:
    assert (
        map_acknowledgement_unavailable_reason_to_ui_message(
            "Outbound acknowledgement is disabled."
        )
        == "Outbound acknowledgement is disabled."
    )


@pytest.mark.parametrize(
    "reason",
    [
        None,
        "Twilio account SID is not configured.",
        "Twilio auth token is not configured.",
        "Twilio WhatsApp sender is not configured.",
        "Outbound acknowledgement requires Postgres storage.",
        "Outbound acknowledgement tenant binding is not configured.",
    ],
)
def test_unavailable_reason_maps_to_provider_neutral_ui_message(
    reason: str | None,
) -> None:
    message = map_acknowledgement_unavailable_reason_to_ui_message(reason)

    assert message == "Outbound acknowledgement is not fully configured."
    assert "Twilio" not in message
    assert "twilio" not in message
    assert "provider" not in message
    assert "provider_message_id" not in message
    assert "error_code" not in message
    assert "account SID" not in message
    assert "auth token" not in message
    assert "sender" not in message
