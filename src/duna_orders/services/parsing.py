from duna_orders.domain.models import ParseLogEntry, ParseResult, Product
from duna_orders.ids import new_id
from duna_orders.parsing.base import ParserInterface
from duna_orders.parsing.exceptions import ParserError
from duna_orders.storage.base import StorageInterface
from duna_orders.parsing.prompts import PROMPT_VERSION

class ParsingService:
    """Orchestrates parser invocation and parse_log persistence."""

    def __init__(self, parser: ParserInterface, storage: StorageInterface) -> None:
        self._parser = parser
        self._storage = storage

    def parse(self, raw_message: str, products: list[Product]) -> ParseResult:
        try:
            result = self._parser.parse(raw_message, products)
        except ParserError as error:
            self._storage.append_parse_log(
                ParseLogEntry(
                    parse_id=new_id("prs"),
                    raw_message=raw_message,
                    parsed_json="",
                    model=self._parser.model_name,
                    prompt_version=PROMPT_VERSION,
                    latency_ms=0,
                    success=False,
                    error=str(error),
                )
            )
            raise

        self._storage.append_parse_log(
            ParseLogEntry(
                parse_id=new_id("prs"),
                raw_message=raw_message,
                parsed_json=result.request.model_dump_json(),
                model=result.model,
                prompt_version=PROMPT_VERSION,
                latency_ms=result.latency_ms,
                success=True,
                error=None,
            )
        )

        return result