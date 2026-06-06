from twilio.request_validator import RequestValidator


def validate_twilio_signature(
    *,
    url: str,
    form_params: dict[str, str],
    signature: str | None,
    auth_token: str | None,
) -> bool:
    if not auth_token or not signature:
        return False

    validator = RequestValidator(auth_token)
    return validator.validate(url, form_params, signature)