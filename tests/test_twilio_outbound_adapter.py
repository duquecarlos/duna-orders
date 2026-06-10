from __future__ import annotations

from dataclasses import dataclass

from requests import Timeout
from twilio.base.exceptions import TwilioRestException

from duna_orders.integrations.twilio_outbound import TwilioOutboundMessageAdapter


@dataclass
class FakeMessage:
    sid: str | None


class FakeMessages:
    def __init__(self, *, response: FakeMessage | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, str]] = []

    def create(self, *, from_: str, to: str, body: str) -> FakeMessage:
        self.calls.append({"from_": from_, "to": to, "body": body})
        if self.error is not None:
            raise self.error

        assert self.response is not None
        return self.response


@dataclass
class FakeClient:
    messages: FakeMessages


def test_twilio_outbound_success_maps_message_sid_to_provider_message_id() -> None:
    messages = FakeMessages(response=FakeMessage(sid="SM_SENT"))
    adapter = TwilioOutboundMessageAdapter(
        account_sid="AC_TEST",
        auth_token="auth-token",
        client=FakeClient(messages=messages),
    )

    result = adapter.send_message(
        from_number="whatsapp:+15551234567",
        to_number="whatsapp:+573001112233",
        body="Hola",
    )

    assert result.outcome == "success"
    assert result.provider_message_id == "SM_SENT"
    assert result.error_code is None
    assert result.error_message is None
    assert messages.calls == [
        {
            "from_": "whatsapp:+15551234567",
            "to": "whatsapp:+573001112233",
            "body": "Hola",
        }
    ]


def test_twilio_outbound_timeout_maps_to_unknown() -> None:
    adapter = TwilioOutboundMessageAdapter(
        account_sid="AC_TEST",
        auth_token="auth-token",
        client=FakeClient(messages=FakeMessages(error=Timeout("timed out"))),
    )

    result = adapter.send_message(
        from_number="whatsapp:+15551234567",
        to_number="whatsapp:+573001112233",
        body="Hola",
    )

    assert result.outcome == "unknown"
    assert result.provider_message_id is None


def test_twilio_outbound_server_error_maps_to_unknown() -> None:
    adapter = TwilioOutboundMessageAdapter(
        account_sid="AC_TEST",
        auth_token="auth-token",
        client=FakeClient(
            messages=FakeMessages(
                error=TwilioRestException(
                    status=503,
                    uri="/Messages.json",
                    msg="server unavailable",
                    code=20500,
                )
            )
        ),
    )

    result = adapter.send_message(
        from_number="whatsapp:+15551234567",
        to_number="whatsapp:+573001112233",
        body="Hola",
    )

    assert result.outcome == "unknown"
    assert result.error_code == "20500"


def test_twilio_outbound_generic_unclear_exception_maps_to_unknown() -> None:
    adapter = TwilioOutboundMessageAdapter(
        account_sid="AC_TEST",
        auth_token="auth-token",
        client=FakeClient(messages=FakeMessages(error=RuntimeError("connection reset"))),
    )

    result = adapter.send_message(
        from_number="whatsapp:+15551234567",
        to_number="whatsapp:+573001112233",
        body="Hola",
    )

    assert result.outcome == "unknown"
    assert result.error_code == "RuntimeError"


def test_twilio_outbound_definitive_rejection_maps_to_failed() -> None:
    adapter = TwilioOutboundMessageAdapter(
        account_sid="AC_TEST",
        auth_token="auth-token",
        client=FakeClient(
            messages=FakeMessages(
                error=TwilioRestException(
                    status=400,
                    uri="/Messages.json",
                    msg="invalid number",
                    code=21211,
                )
            )
        ),
    )

    result = adapter.send_message(
        from_number="whatsapp:+15551234567",
        to_number="whatsapp:+573001112233",
        body="Hola",
    )

    assert result.outcome == "failed"
    assert result.error_code == "21211"


def test_twilio_outbound_auth_error_maps_to_failed() -> None:
    adapter = TwilioOutboundMessageAdapter(
        account_sid="AC_TEST",
        auth_token="auth-token",
        client=FakeClient(
            messages=FakeMessages(
                error=TwilioRestException(
                    status=401,
                    uri="/Messages.json",
                    msg="auth failed",
                    code=20003,
                )
            )
        ),
    )

    result = adapter.send_message(
        from_number="whatsapp:+15551234567",
        to_number="whatsapp:+573001112233",
        body="Hola",
    )

    assert result.outcome == "failed"
    assert result.error_code == "20003"


def test_twilio_outbound_result_does_not_expose_raw_twilio_response() -> None:
    adapter = TwilioOutboundMessageAdapter(
        account_sid="AC_TEST",
        auth_token="auth-token",
        client=FakeClient(messages=FakeMessages(response=FakeMessage(sid="SM_SENT"))),
    )

    result = adapter.send_message(
        from_number="whatsapp:+15551234567",
        to_number="whatsapp:+573001112233",
        body="Hola",
    )

    assert not hasattr(result, "raw_response")
    assert not hasattr(result, "twilio_response")
