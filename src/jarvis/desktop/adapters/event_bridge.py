"""Event bridge: core async events → Qt signals on the shared AppState.

The bridge is the ONLY connection between the core event bus and the UI.
It runs on the qasync loop (same thread as Qt), so direct signal emission
is thread-safe. Subscriptions are isolated: bridge failures are logged by
the bus and never propagate into core services.
"""

from __future__ import annotations

from jarvis.core.event_bus import EventBus
from jarvis.core.events import (
    ApprovalDecided,
    ApprovalRequested,
    AssistantStateChanged,
    ChatStreamCancelled,
    ChatStreamCompleted,
    ChatStreamFailed,
    ChatStreamStarted,
    ChatToken,
    KillSwitchRearmed,
    KillSwitchTriggered,
    ProviderHealthChanged,
    ServiceStatusChanged,
    ShutdownCompleted,
    StartupCompleted,
)
from jarvis.desktop.viewmodels.app_state import AppState


class EventBridge:
    """Translate core events into AppState signals."""

    def __init__(self, bus: EventBus, state: AppState) -> None:
        self._bus = bus
        self._state = state
        self._subscribe_all()

    def _subscribe_all(self) -> None:
        self._bus.subscribe(ServiceStatusChanged, self._on_service_status)
        self._bus.subscribe(ProviderHealthChanged, self._on_provider_health)
        self._bus.subscribe(StartupCompleted, self._on_startup)
        self._bus.subscribe(ShutdownCompleted, self._on_shutdown)
        self._bus.subscribe(KillSwitchTriggered, self._on_kill_on)
        self._bus.subscribe(KillSwitchRearmed, self._on_kill_off)
        self._bus.subscribe(ApprovalRequested, self._on_approval_requested)
        self._bus.subscribe(ApprovalDecided, self._on_approval_decided)
        self._bus.subscribe(ChatStreamStarted, self._on_chat_started)
        self._bus.subscribe(ChatToken, self._on_chat_token)
        self._bus.subscribe(ChatStreamCompleted, self._on_chat_completed)
        self._bus.subscribe(ChatStreamFailed, self._on_chat_failed)
        self._bus.subscribe(ChatStreamCancelled, self._on_chat_cancelled)
        self._bus.subscribe(AssistantStateChanged, self._on_assistant_state)

    # -- Handlers (sync; the bus awaits if needed) ------------------------------

    def _on_service_status(self, event: ServiceStatusChanged) -> None:
        self._state.apply_service_status(
            event.service, event.status, event.detail
        )

    def _on_provider_health(self, event: ProviderHealthChanged) -> None:
        self._state.apply_provider_health(
            event.provider, event.available, event.detail
        )

    def _on_startup(self, event: StartupCompleted) -> None:
        self._state.set_engine_state("running")
        self._state.push_notification("info", "JARVIS core engine started")

    def _on_shutdown(self, event: ShutdownCompleted) -> None:
        self._state.set_engine_state("stopped")

    def _on_kill_on(self, event: KillSwitchTriggered) -> None:
        self._state.set_kill_switch(True)
        self._state.set_engine_state("kill_switch")
        self._state.push_notification(
            "danger", f"EMERGENCY STOP: {event.reason or 'triggered'}"
        )

    def _on_kill_off(self, event: KillSwitchRearmed) -> None:
        self._state.set_kill_switch(False)
        self._state.set_engine_state("running")
        self._state.push_notification("info", "Kill switch re-armed")

    def _on_approval_requested(self, event: ApprovalRequested) -> None:
        self._state.push_notification(
            "warning", f"Approval requested: {event.action or event.summary}"
        )

    def _on_approval_decided(self, event: ApprovalDecided) -> None:
        verb = "approved" if event.approved else "denied"
        self._state.push_notification(
            "info", f"Action {event.request_id[:8] or '(unknown)'} {verb}"
        )

    # -- Chat streaming (Stage 2.6) ---------------------------------------------

    def _on_chat_started(self, event: ChatStreamStarted) -> None:
        self._state.apply_chat_stream_started(
            event.request_id, event.provider, event.model, event.privacy
        )

    def _on_chat_token(self, event: ChatToken) -> None:
        self._state.apply_chat_token(event.request_id, event.seq, event.text)

    def _on_chat_completed(self, event: ChatStreamCompleted) -> None:
        self._state.apply_chat_stream_completed(
            event.request_id, event.content, event.tokens_used
        )

    def _on_chat_failed(self, event: ChatStreamFailed) -> None:
        self._state.apply_chat_stream_failed(
            event.request_id, event.detail, event.partial_content
        )

    def _on_chat_cancelled(self, event: ChatStreamCancelled) -> None:
        self._state.apply_chat_stream_cancelled(
            event.request_id, event.partial_content
        )

    def _on_assistant_state(self, event: AssistantStateChanged) -> None:
        self._state.set_assistant_state(event.state)
