class ParserError(Exception):
    """Base for all parser failures."""


class MissingAPIKeyError(ParserError):
    """Raised when the concrete parser requires an API key that is not set."""


class ParserAPIError(ParserError):
    """Raised when the underlying API call fails."""


class ParserOutputError(ParserError):
    """Raised when the parser produces output that does not validate."""

    def __init__(self, raw_response: str, validation_error: str) -> None:
        super().__init__(f"Parser produced invalid output: {validation_error}")
        self.raw_response = raw_response
        self.validation_error = validation_error