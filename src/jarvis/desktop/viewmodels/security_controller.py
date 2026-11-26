"""SecurityController bridging SecurityView to backend security services."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any

from PySide6.QtCore import QObject, Signal

from jarvis.core.lifecycle import LifecycleManager
from jarvis.desktop.viewmodels.app_state import AppState
from jarvis.monitoring.logging import get_logger
from jarvis.security.allowlist import CommandAllowlist
from jarvis.security.audit import AuditService, redact_audit_detail
from jarvis.security.permissions import Capability, PermissionPolicy
from jarvis.tools import ToolRegistry

log = get_logger(__name__)

_FORBIDDEN_SHELL_CHARS = frozenset("&|;<>`$()\n\r")


class SecurityController(QObject):
    """Validated UI controller for permissions, allowlist, audit & kill switch."""

    security_changed = Signal()

    def __init__(
        self,
        state: AppState,
        *,
        policy: PermissionPolicy | None = None,
        allowlist: CommandAllowlist | None = None,
        audit: AuditService | None = None,
        registry: ToolRegistry | None = None,
        lifecycle: LifecycleManager | None = None,
    ) -> None:
        super().__init__()
        self._state = state
        self._policy = policy
        self._allowlist = allowlist
        self._audit = audit
        self._registry = registry
        self._lifecycle = lifecycle
        self._tasks: set[asyncio.Task[Any]] = set()

    def list_capabilities(self) -> list[tuple[Capability, bool]]:
        granted = self._policy.granted_capabilities if self._policy else set()
        return [(cap, cap in granted) for cap in Capability]

    def set_capability_enabled(self, cap: Capability, enabled: bool) -> None:
        if self._policy is None:
            return
        if enabled:
            self._policy.granted_capabilities.add(cap)
        else:
            self._policy.granted_capabilities.discard(cap)
        self.security_changed.emit()

    def list_tools(self) -> list[dict[str, str]]:
        if self._registry is None:
            return []
        return [
            {
                "tool_id": spec.tool_id,
                "risk": spec.risk_level.value,
                "caps": ", ".join(c.value for c in sorted(spec.capabilities)),
            }
            for spec in self._registry.list_tools()
        ]

    def list_allowlist(self) -> list[str]:
        if self._allowlist is None:
            return []
        exact = sorted(self._allowlist.exact)
        patterns = sorted(f"pattern:{p}" for p in self._allowlist.patterns)
        return exact + patterns

    def add_allowlist_command(self, command: str) -> bool:
        clean = command.strip()
        if (
            not clean
            or self._allowlist is None
            or any(ch in _FORBIDDEN_SHELL_CHARS for ch in clean)
        ):
            return False
        self._allowlist.add_exact(clean)
        self.security_changed.emit()
        return True

    def remove_allowlist_entry(self, entry: str) -> bool:
        if self._allowlist is None:
            return False
        raw = entry.removeprefix("pattern:")
        removed = self._allowlist.remove(raw)
        if removed:
            self.security_changed.emit()
        return removed

    def list_audit_entries(
        self, *, tool_filter: str = "", result_filter: str = ""
    ) -> list[dict[str, Any]]:
        if self._audit is None:
            return []
        tf = tool_filter.strip().lower()
        rf = result_filter.strip().lower()
        entries: list[dict[str, Any]] = []
        for entry in reversed(self._audit.read_all()):
            action = str(entry.get("action") or "")
            res = str(entry.get("result") or "")
            if tf and tf not in action.lower():
                continue
            if rf and rf not in res.lower():
                continue
            entries.append(
                {
                    "seq": entry.get("seq", 0),
                    "action": action,
                    "result": res,
                    "approved_by": entry.get("approved_by", "n/a"),
                    "detail": redact_audit_detail(entry.get("detail") or {}),
                }
            )
            if len(entries) >= 100:
                break
        return entries

    def trigger_kill_switch(self) -> None:
        if self._lifecycle is not None:
            self._spawn(self._lifecycle.trigger_kill_switch("manual_security_ui"))

    def rearm_kill_switch(self) -> None:
        if self._lifecycle is not None:
            self._spawn(self._lifecycle.rearm_kill_switch())

    def _spawn(self, coro: Awaitable[Any]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            close = getattr(coro, "close", None)
            if callable(close):
                close()
            return
        task = loop.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
