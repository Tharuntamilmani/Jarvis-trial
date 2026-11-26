"""Tier 1: In-process working memory (task scratchpad)."""

from __future__ import annotations

from typing import Any


class WorkingMemory:
    """Ephemeral key-value scratchpad scoped to the current task."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    def set(self, key: str, value: Any) -> None:
        clean = key.strip()
        if not clean:
            raise ValueError("working memory key cannot be empty")
        self._store[clean] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._store.get(key, default)

    def delete(self, key: str) -> bool:
        return self._store.pop(key, None) is not None

    def clear(self) -> None:
        self._store.clear()

    def snapshot(self) -> dict[str, Any]:
        return dict(self._store)

    def __len__(self) -> int:
        return len(self._store)
