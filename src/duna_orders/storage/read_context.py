from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from types import TracebackType


@dataclass(frozen=True)
class _SheetsRequestState:
    storage: object
    record_set: object


_current_sheets_request_state: ContextVar[_SheetsRequestState | None] = ContextVar(
    "current_sheets_request_state",
    default=None,
)


class SheetsRequestContext:
    def __init__(self, storage: object) -> None:
        self._storage = storage
        self._token: Token[_SheetsRequestState | None] | None = None

    def __enter__(self) -> "SheetsRequestContext":
        if _current_sheets_request_state.get() is not None:
            raise RuntimeError("Nested sheets request contexts are not supported.")

        new_record_set = getattr(self._storage, "_new_record_set", None)

        if callable(new_record_set):
            state = _SheetsRequestState(
                storage=self._storage,
                record_set=new_record_set(),
            )
            self._token = _current_sheets_request_state.set(state)

        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if self._token is not None:
            _current_sheets_request_state.reset(self._token)
            self._token = None

        return False


def current_sheets_record_set(storage: object) -> object | None:
    state = _current_sheets_request_state.get()

    if state is None or state.storage is not storage:
        return None

    return state.record_set


def sheets_request_context(storage: object) -> SheetsRequestContext:
    return SheetsRequestContext(storage)