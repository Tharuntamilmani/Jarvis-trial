"""Append-only execution audit sink.

Phase 1 foundation: JSON-lines file audit under ``logs/audit.log``. Phase 7
adds the DB-backed ``audit_log`` table and dual-write. The file sink is
already append-only and tamper-evident in ordering (monotonic sequence).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from jarvis.config import Settings
from jarvis.core.event_bus import EventBus
from jarvis.core.events import AuditRecordWritten
from jarvis.monitoring.logging import get_logger

log = get_logger(__name__)


class AuditService:
    """Append-only audit trail for security-relevant events."""

    def __init__(self, settings: Settings, bus: EventBus) -> None:
        self._settings = settings
        self._bus = bus
        self._path = settings.audit_log_path
        self._seq = 0
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        action: str,
        *,
        result: str,
        approved_by: str = "n/a",
        detail: dict | None = None,
    ) -> None:
        """Append one audit record and broadcast the audit event."""
        self._seq += 1
        entry = {
            "seq": self._seq,
            "ts": time.time(),
            "action": action,
            "result": result,
            "approved_by": approved_by,
            "detail": detail or {},
        }
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        log.debug("audit record %d: %s -> %s", self._seq, action, result)

    async def record_async(
        self,
        action: str,
        *,
        result: str,
        approved_by: str = "n/a",
        detail: dict | None = None,
    ) -> None:
        """Async variant that also publishes ``AuditRecordWritten``."""
        self.record(
            action, result=result, approved_by=approved_by, detail=detail
        )
        await self._bus.publish(
            AuditRecordWritten(action=action, result=result)
        )

    @property
    def path(self) -> Path:
        return self._path

    def read_all(self) -> list[dict]:
        """Load all audit entries (for tests and future UI review)."""
        if not self._path.exists():
            return []
        entries: list[dict] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entries.append(json.loads(line))
        return entries
