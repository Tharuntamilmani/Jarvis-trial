"""Tier 2: In-process bounded short-term memory ring buffer."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class ShortTermEntry:
    """One recent turn entry held in short-term memory."""

    role: str
    content: str
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class ShortTermMemory:
    """Bounded ring buffer of recent conversation turns (local-only)."""

    def __init__(self, max_entries: int = 20) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._entries: deque[ShortTermEntry] = deque(maxlen=max_entries)

    @property
    def max_entries(self) -> int:
        return self._entries.maxlen or 0

    def append(self, role: str, content: str) -> None:
        clean = content.strip()
        if not clean:
            return
        self._entries.append(ShortTermEntry(role=role, content=clean))

    def recent(self, limit: int | None = None) -> list[ShortTermEntry]:
        items = list(self._entries)
        if limit is not None and limit >= 0:
            return items[-limit:] if limit > 0 else []
        return items

    def build_context(self, *, max_chars: int = 4000) -> str:
        """Format recent entries within ``max_chars`` for local context."""
        selected: list[str] = []
        total = 0
        for entry in reversed(self._entries):
            line = f"{entry.role}: {entry.content}"
            if selected and total + len(line) > max_chars:
                break
            selected.append(line)
            total += len(line)
        selected.reverse()
        return "\n".join(selected)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
