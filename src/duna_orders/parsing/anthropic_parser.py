import json
import time

from anthropic import Anthropic
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from duna_orders.config import settings
from duna_orders.domain.models import DraftOrderRequest, ParseResult, Product
from duna_orders.parsing.base import ParserInterface
from duna_orders.parsing.exceptions import (
    MissingAPIKeyError,
    ParserAPIError,
    ParserOutputError,
)
from duna_orders.parsing.prompts import SYSTEM_PROMPT, build_user_prompt


def _extract_json_text(raw_response: str) -> str:
    text = raw_response.strip()

    if text.startswith("```"):
        lines = text.splitlines()

        if lines and lines[0].startswith("```"):
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        text = "\n".join(lines).strip()

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1 or end <= start:
        return text

    return text[start : end + 1]


class AnthropicParser(ParserInterface):
    def __init__(self) -> None:
        if not settings.anthropic_api_key:
            raise MissingAPIKeyError(
                "ANTHROPIC_API_KEY is not set. Add it to .env to use the parser."
            )

        self._client = Anthropic(api_key=settings.anthropic_api_key)
        self._model = settings.llm_model

    @property
    def model_name(self) -> str:
        return self._model

    @retry(
        retry=retry_if_exception_type(ParserAPIError),
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        reraise=True,
    )
    def parse(self, raw_message: str, products: list[Product]) -> ParseResult:
        user_prompt = build_user_prompt(raw_message, products)
        start = time.perf_counter()

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=settings.llm_max_tokens,
                temperature=settings.llm_temperature,
                system=SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": user_prompt,
                    }
                ],
            )
        except Exception as error:
            raise ParserAPIError(str(error)) from error

        latency_ms = int((time.perf_counter() - start) * 1000)
        raw_response = response.content[0].text
        json_text = _extract_json_text(raw_response)

        try:
            parsed = json.loads(json_text)
            request = DraftOrderRequest.model_validate(parsed["request"])
            warnings = parsed.get("warnings", [])
        except Exception as error:
            raise ParserOutputError(raw_response, str(error)) from error

        return ParseResult(
            request=request,
            warnings=warnings,
            model=self._model,
            latency_ms=latency_ms,
            raw_response=raw_response,
        )