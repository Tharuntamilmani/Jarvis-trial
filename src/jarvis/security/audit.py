"""Append-only execution audit sink with PostgreSQL dual-write & redaction."""

from __future__ import annotations

import json
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import desc, select

from jarvis.config import Settings
from jarvis.core.event_bus import EventBus
from jarvis.core.events import AuditRecordWritten
from jarvis.core.exceptions import JarvisError
from jarvis.database.models import AuditLog
from jarvis.monitoring.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from jarvis.database.connection import DatabaseService

log = get_logger(__name__)

_SENSITIVE_KEY_PARTS = (
    "secret",
    "token",
    "password",
    "api_key",
    "apikey",
    "clipboard",
    "screenshot",
    "frame",
    "embedding",
    "file_content",
    "pcm",
)


class AuditPersistenceError(JarvisError):
    """Raised when required durable audit persistence fails."""


def redact_audit_detail(detail: dict[str, Any] | None) -> dict[str, Any]:
    """Return a copy of ``detail`` with sensitive/binary fields redacted."""
    if not detail:
        return {}
    clean: dict[str, Any] = {}
    for key, value in detail.items():
        lowered = str(key).lower()
        if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
            clean[key] = "[REDACTED]"
        elif isinstance(value, (bytes, bytearray)):
            clean[key] = f"[BINARY:{len(value)}B]"
        elif isinstance(value, dict):
            clean[key] = redact_audit_detail(value)
        else:
            clean[key] = value
    return clean


class AuditService:
    """Append-only audit trail (JSON-lines + PostgreSQL ``audit_log``)."""

    def __init__(
        self,
        settings: Settings,
        bus: EventBus,
        *,
        database: DatabaseService | None = None,
    ) -> None:
        self._settings = settings
        self._bus = bus
        self._db = database
        self._path = settings.audit_log_path
        self._seq = 0
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def attach_database(self, database: DatabaseService | None) -> None:
        self._db = database

    def record(
        self,
        action: str,
        *,
        result: str,
        approved_by: str = "n/a",
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Append one redacted audit record to the local JSONL sink."""
        self._seq += 1
        safe_detail = redact_audit_detail(detail)
        entry = {
            "seq": self._seq,
            "ts": time.time(),
            "action": action,
            "result": result,
            "approved_by": approved_by,
            "detail": safe_detail,
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
        detail: dict[str, Any] | None = None,
        tool: str | None = None,
        require_db: bool = False,
    ) -> None:
        """Dual-write redacted audit record to JSONL and PostgreSQL ``audit_log``."""
        safe_detail = redact_audit_detail(detail)
        self.record(
            action, result=result, approved_by=approved_by, detail=safe_detail
        )
        if self._db is None:
            if require_db:
                raise AuditPersistenceError(
                    "Authoritative PostgreSQL audit database is unavailable"
                )
        else:
            try:
                async with self._db.session() as session:
                    session.add(
                        AuditLog(
                            action=action[:120],
                            tool=(tool or action)[:120],
                            args=safe_detail,
                            approved_by=approved_by[:60],
                            result=result,
                        )
                    )
            except Exception as exc:
                if require_db:
                    raise AuditPersistenceError(
                        f"PostgreSQL audit write failed: {type(exc).__name__}"
                    ) from exc
                log.warning("non-fatal audit_log DB write failed: %s", exc)

        await self._bus.publish(
            AuditRecordWritten(action=action, result=result)
        )

    async def query_db(
        self,
        *,
        tool: str | None = None,
        actor_id: str | None = None,
        risk_level: str | None = None,
        result: str | None = None,
        correlation_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query append-only ``audit_log`` records with optional filters."""
        if self._db is None:
            return []
        stmt = select(AuditLog).order_by(desc(AuditLog.id)).limit(max(1, limit))
        if tool:
            stmt = stmt.where(AuditLog.tool == tool)
        if result:
            stmt = stmt.where(AuditLog.result == result)
        async with self._db.session() as session:
            rows = (await session.scalars(stmt)).all()
        records: list[dict[str, Any]] = []
        for row in rows:
            args = row.args or {}
            if actor_id and args.get("actor_id") != actor_id:
                continue
            if risk_level and args.get("risk_level") != risk_level:
                continue
            if correlation_id and args.get("correlation_id") != correlation_id:
                continue
            records.append(
                {
                    "id": row.id,
                    "action": row.action,
                    "tool": row.tool,
                    "args": args,
                    "approved_by": row.approved_by,
                    "result": row.result,
                    "created_at": row.created_at.isoformat() if row.created_at else "",
                }
            )
        return records

    @property
    def path(self) -> Path:
        return self._path

    def read_all(self) -> list[dict[str, Any]]:
        """Load all local JSONL audit entries."""
        if not self._path.exists():
            return []
        entries: list[dict[str, Any]] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entries.append(json.loads(line))
        return entries
