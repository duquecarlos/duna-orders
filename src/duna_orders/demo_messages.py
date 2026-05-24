from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


DEFAULT_DEMO_MESSAGES_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "demo_messages.json"
)


class DemoMessagesLoadError(RuntimeError):
    pass


class DemoMessageEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    message: str = Field(min_length=1)
    description: str = Field(min_length=1)


class DemoMessagesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[DemoMessageEntry]


def load_demo_messages(path: str | Path | None = None) -> DemoMessagesFile:
    messages_path = Path(path) if path is not None else DEFAULT_DEMO_MESSAGES_PATH

    try:
        raw_data: Any = json.loads(messages_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DemoMessagesLoadError(f"Demo messages file not found: {messages_path}") from exc
    except json.JSONDecodeError as exc:
        raise DemoMessagesLoadError(
            f"Invalid JSON in demo messages {messages_path}: {exc.msg}"
        ) from exc

    try:
        return DemoMessagesFile.model_validate(raw_data)
    except ValidationError as exc:
        raise DemoMessagesLoadError(
            f"Invalid demo messages schema in {messages_path}: {exc}"
        ) from exc