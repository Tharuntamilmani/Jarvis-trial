"""Shared application state for all three windows.

Single source of truth for UI-facing state, fed exclusively by the
EventBridge from core events. Windows never fetch core state themselves.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal


_ENGINE_SERVICE_STATUS = {
    "running": ("healthy", "running"),
    "starting": ("starting", "starting"),
    "stopped": ("stopped", "stopped"),
    "kill_switch": ("degraded", "emergency stop"),
}


class AppState(QObject):
    """Observable shared state (one instance for the whole app)."""

    engine_state_changed = Signal(str)          # running | starting | stopped | kill_switch
    service_status_changed = Signal(str, str, str)   # service, status, detail
    provider_health_changed = Signal(str, bool, str) # provider, available, detail
    kill_switch_changed = Signal(bool)
    assistant_state_changed = Signal(str)       # idle | listening | thinking | speaking
    notification = Signal(str, str)             # level, message

    # Chat streaming (Stage 2.6) — forwarded by the EventBridge.
    chat_stream_started = Signal(str, str, str, str)  # request, provider, model, privacy
    chat_token = Signal(str, int, str)                # request, seq, text
    chat_stream_completed = Signal(str, str, int)     # request, content, tokens
    chat_stream_failed = Signal(str, str, str)        # request, detail, partial
    chat_stream_cancelled = Signal(str, str)          # request, partial

    def __init__(self) -> None:
        super().__init__()
        self.engine_state: str = "stopped"
        self.service_statuses: dict[str, str] = {}
        self.service_details: dict[str, str] = {}
        self.provider_health: dict[str, dict[str, Any]] = {}
        self.kill_switch_active: bool = False
        self.assistant_state: str = "idle"

    # -- Mutators (called by the bridge only) -----------------------------------

    def set_engine_state(self, state: str) -> None:
        self.engine_state = state
        self.engine_state_changed.emit(state)
        status, detail = _ENGINE_SERVICE_STATUS.get(state, ("unknown", state))
        self.apply_service_status("engine", status, detail)

    def apply_service_status(self, service: str, status: str, detail: str = "") -> None:
        self.service_statuses[service] = status
        self.service_details[service] = detail
        self.service_status_changed.emit(service, status, detail)

    def apply_provider_health(self, provider: str, available: bool, detail: str) -> None:
        self.provider_health[provider] = {
            "available": available,
            "detail": detail,
        }
        self.provider_health_changed.emit(provider, available, detail)

        # The shared "providers" chip is an aggregate signal: at least one
        # reachable provider means routing is still possible. Individual
        # provider rows remain driven by provider_health_changed.
        total = len(self.provider_health)
        if total == 0:
            return
        online = sum(
            1 for info in self.provider_health.values() if info["available"]
        )
        self.apply_service_status(
            "providers",
            "healthy" if online else "unavailable",
            f"{online}/{total} available",
        )

    def set_kill_switch(self, active: bool) -> None:
        self.kill_switch_active = active
        self.kill_switch_changed.emit(active)

    def set_assistant_state(self, state: str) -> None:
        self.assistant_state = state
        self.assistant_state_changed.emit(state)

    def push_notification(self, level: str, message: str) -> None:
        self.notification.emit(level, message)

    # -- Chat mutators (called by the bridge only) --------------------------------

    def apply_chat_stream_started(
        self, request_id: str, provider: str, model: str, privacy: str
    ) -> None:
        self.chat_stream_started.emit(request_id, provider, model, privacy)

    def apply_chat_token(self, request_id: str, seq: int, text: str) -> None:
        self.chat_token.emit(request_id, seq, text)

    def apply_chat_stream_completed(
        self, request_id: str, content: str, tokens_used: int
    ) -> None:
        self.chat_stream_completed.emit(request_id, content, tokens_used)

    def apply_chat_stream_failed(
        self, request_id: str, detail: str, partial_content: str
    ) -> None:
        self.chat_stream_failed.emit(request_id, detail, partial_content)

    def apply_chat_stream_cancelled(
        self, request_id: str, partial_content: str
    ) -> None:
        self.chat_stream_cancelled.emit(request_id, partial_content)

    # -- Snapshot helpers ----------------------------------------------------------

    def status_snapshot(self) -> dict[str, str]:
        return dict(self.service_statuses)

    def provider_snapshot(self) -> dict[str, dict[str, Any]]:
        return {
            name: dict(info) for name, info in self.provider_health.items()
        }
