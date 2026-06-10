from __future__ import annotations

from typing import Protocol

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from duna_orders.services.outbound_acknowledgement import OutboundProviderResult

try:
    from requests import Timeout as RequestsTimeout
except ImportError:  # pragma: no cover - requests is a Twilio dependency
    RequestsTimeout = TimeoutError  # type: ignore[assignment]


class _TwilioMessageResource(Protocol):
    sid: str | None


class _TwilioMessagesClient(Protocol):
    def create(
        self,
        *,
        from_: str,
        to: str,
        body: str,
    ) -> _TwilioMessageResource:
        ...


class _TwilioClient(Protocol):
    messages: _TwilioMessagesClient


class TwilioOutboundMessageAdapter:
    def __init__(self, *, account_sid: str, auth_token: str, client: _TwilioClient | None = None) -> None:
        _require_text(account_sid, "account_sid")
        _require_text(auth_token, "auth_token")
        self._client = client or Client(account_sid, auth_token)

    def send_message(
        self,
        *,
        from_number: str,
        to_number: str,
        body: str,
    ) -> OutboundProviderResult:
        try:
            message = self._client.messages.create(
                from_=from_number,
                to=to_number,
                body=body,
            )
        except (TimeoutError, RequestsTimeout) as error:
            return OutboundProviderResult.unknown(
                error_code=type(error).__name__,
                error_message="Twilio request timed out before the send result was known.",
            )
        except TwilioRestException as error:
            return _result_from_twilio_error(error)
        except Exception as error:  # noqa: BLE001
            return OutboundProviderResult.unknown(
                error_code=type(error).__name__,
                error_message="Twilio send result is unknown.",
            )

        provider_message_id = getattr(message, "sid", None)
        if not provider_message_id:
            return OutboundProviderResult.unknown(
                error_code="missing_provider_message_id",
                error_message="Twilio accepted no message id in the response.",
            )

        return OutboundProviderResult.success(provider_message_id=provider_message_id)


def _result_from_twilio_error(error: TwilioRestException) -> OutboundProviderResult:
    status = getattr(error, "status", None)
    code = getattr(error, "code", None)
    error_code = str(code or status or type(error).__name__)

    if status in {400, 401, 403, 404}:
        return OutboundProviderResult.failed(
            error_code=error_code,
            error_message="Twilio definitively rejected the outbound message.",
        )

    return OutboundProviderResult.unknown(
        error_code=error_code,
        error_message="Twilio send result is unknown.",
    )


def _require_text(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name} is required")
