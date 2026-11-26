"""Startup crash recovery & interrupted-operation reconciliation (Phase 8.5)."""

from __future__ import annotations

from typing import Any

from jarvis.monitoring.logging import get_logger
from jarvis.security.audit import AuditService

log = get_logger(__name__)

_TERMINAL_RESULTS = frozenset(
    {
        "completed",
        "failed",
        "timeout",
        "cancelled",
        "denied_approval",
        "denied_permission",
        "denied_allowlist",
        "denied_kill_switch",
        "denied_or_rejected",
        "interrupted_no_replay",
        "interrupted_read_only",
    }
)


class CrashRecoveryManager:
    """Reconciles interrupted operations without ever replaying destructive tools."""

    def __init__(
        self,
        *,
        audit: AuditService | None = None,
        conversations: Any | None = None,
    ) -> None:
        self._audit = audit
        self._conversations = conversations

    async def reconcile(self) -> dict[str, Any]:
        """Reconcile stale conversations and interrupted tool executions safely."""
        stale_conversations = 0
        if self._conversations is not None:
            fn = getattr(self._conversations, "reconcile_stale_generations", None)
            if callable(fn):
                try:
                    stale_conversations = int(await fn() or 0)
                except Exception:  # noqa: BLE001
                    log.warning("conversation crash reconciliation failed", exc_info=True)

        interrupted_non_replayable: list[str] = []
        interrupted_read_only: list[str] = []

        if self._audit is not None:
            entries = self._audit.read_all()
            by_call: dict[str, list[dict[str, Any]]] = {}
            for entry in entries:
                detail = entry.get("detail") or {}
                call_id = str(detail.get("call_id") or "")
                if call_id:
                    by_call.setdefault(call_id, []).append(entry)

            for call_id, call_entries in by_call.items():
                results = {str(e.get("result") or "") for e in call_entries}
                if "started" not in results or (results & _TERMINAL_RESULTS):
                    continue
                first = call_entries[0]
                detail = first.get("detail") or {}
                risk = str(detail.get("risk_level") or "destructive")
                action = str(first.get("action") or "unknown")
                if risk == "read_only":
                    interrupted_read_only.append(call_id)
                    await self._audit.record_async(
                        action,
                        result="interrupted_read_only",
                        detail={"call_id": call_id, "risk_level": risk},
                    )
                else:
                    interrupted_non_replayable.append(call_id)
                    await self._audit.record_async(
                        action,
                        result="interrupted_no_replay",
                        detail={"call_id": call_id, "risk_level": risk},
                    )

        return {
            "stale_conversations": stale_conversations,
            "interrupted_non_replayable": interrupted_non_replayable,
            "interrupted_read_only": interrupted_read_only,
            "replayed_count": 0,  # Never auto-replay consequential/destructive tools
        }
