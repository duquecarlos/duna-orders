from duna_orders.domain.models import (
    DraftOrderRequest,
    ParseResult,
    Product,
)
from duna_orders.parsing.base import ParserInterface


class MockParser(ParserInterface):
    """Deterministic parser for tests. Configurable via constructor."""

    def __init__(
        self,
        result: ParseResult | None = None,
        raise_error: Exception | None = None,
        model_name: str = "mock-parser",
    ) -> None:
        self._result = result
        self._raise_error = raise_error
        self._model_name = model_name
        self.calls: list[tuple[str, list[Product]]] = []

    @property
    def model_name(self) -> str:
        return self._model_name

    def parse(self, raw_message: str, products: list[Product]) -> ParseResult:
        self.calls.append((raw_message, list(products)))

        if self._raise_error is not None:
            raise self._raise_error

        if self._result is None:
            return ParseResult(
                request=DraftOrderRequest(
                    raw_message=raw_message,
                    customer_name="",
                    items=[],
                ),
                warnings=[],
                model=self._model_name,
                latency_ms=0,
                raw_response="{}",
            )

        return self._result