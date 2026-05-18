from abc import ABC, abstractmethod

from duna_orders.domain.models import ParseResult, Product


class ParserInterface(ABC):
    """Pure function: text + catalog -> ParseResult.

    Implementations must not touch storage, must not modify state,
    must not create orders. Failure cases raise ParserError or a subtype.
    """

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Identifier of the underlying model."""

    @abstractmethod
    def parse(self, raw_message: str, products: list[Product]) -> ParseResult:
        pass