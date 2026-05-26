from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from time import monotonic
from typing import Callable, TypeAlias

Record: TypeAlias = dict[str, object]
CacheKey: TypeAlias = tuple[str, str]


DEFAULT_SHEETS_RECORD_CACHE_TTL_S = 30.0


@dataclass
class _CacheEntry:
    records: list[Record]
    loaded_at: float


@dataclass
class SheetsRecordsCache:
    ttl_s: float = DEFAULT_SHEETS_RECORD_CACHE_TTL_S
    time_source: Callable[[], float] = monotonic
    _entries: dict[CacheKey, _CacheEntry] = field(default_factory=dict)

    def get_or_load(
        self,
        *,
        spreadsheet_id: str,
        sheet_name: str,
        load_records: Callable[[], list[Record]],
    ) -> list[Record]:
        key = (spreadsheet_id, sheet_name)
        now = self.time_source()
        entry = self._entries.get(key)

        if entry is not None and now - entry.loaded_at <= self.ttl_s:
            return deepcopy(entry.records)

        try:
            records = load_records()
        except Exception:
            self._entries.pop(key, None)
            raise

        self._entries[key] = _CacheEntry(
            records=deepcopy(records),
            loaded_at=now,
        )
        return deepcopy(records)

    def invalidate(self, *, spreadsheet_id: str, sheet_name: str) -> None:
        self._entries.pop((spreadsheet_id, sheet_name), None)

    def invalidate_many(self, *, spreadsheet_id: str, sheet_names: list[str]) -> None:
        for sheet_name in sheet_names:
            self.invalidate(spreadsheet_id=spreadsheet_id, sheet_name=sheet_name)

    def clear(self) -> None:
        self._entries.clear()