"""Event bridge: core async events → Qt signals on the shared AppState.

The bridge is the ONLY connection between the core event bus and the UI.
It runs on the qasync loop (same thread as Qt), so direct signal emission
is thread-safe. Subscriptions are isolated: bridge failures are logged by
the bus and never propagate into core services.
"""

from __future__ import annotations

from jarvis.core.event_bus import EventBus
from jarvis.core.events import (
    AgentRunFinished,
    AgentRunStarted,
    AgentStepUpdated,
    ApprovalDecided,
    ApprovalRequested,
    AssistantStateChanged,
    AudioDeviceConnected,
    AudioDeviceDisconnected,
    AudioErrorOccurred,
    AudioLevelUpdated,
    AuditRecordWritten,
    AutoLockTriggered,
    CameraConnected,
    CameraDisconnected,
    ChatStreamCancelled,
    ChatStreamCompleted,
    ChatStreamFailed,
    ChatStreamStarted,
    ChatToken,
    GestureRecognized,
    KillSwitchRearmed,
    KillSwitchTriggered,
    PresenceLost,
    ProviderHealthChanged,
    ServiceStatusChanged,
    ShutdownCompleted,
    StartupCompleted,
    TelemetryUpdated,
    UserRecognized,
    VisionErrorOccurred,
    VisionStateChanged,
    VoiceStateChanged,
    VoiceTranscriptPartial,
    VoiceTranscriptReady,
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
        self._bus.subscribe(AgentRunStarted, self._on_agent_started)
        self._bus.subscribe(AgentStepUpdated, self._on_agent_step)
        self._bus.subscribe(AgentRunFinished, self._on_agent_finished)
        self._bus.subscribe(VoiceStateChanged, self._on_voice_state)
        self._bus.subscribe(AudioLevelUpdated, self._on_audio_level)
        self._bus.subscribe(AudioDeviceConnected, self._on_audio_device_connected)
        self._bus.subscribe(AudioDeviceDisconnected, self._on_audio_device_disconnected)
        self._bus.subscribe(AudioErrorOccurred, self._on_audio_error)
        self._bus.subscribe(VoiceTranscriptPartial, self._on_voice_partial)
        self._bus.subscribe(VoiceTranscriptReady, self._on_voice_ready)
        self._bus.subscribe(VisionStateChanged, self._on_vision_state)
        self._bus.subscribe(CameraConnected, self._on_camera_connected)
        self._bus.subscribe(CameraDisconnected, self._on_camera_disconnected)
        self._bus.subscribe(VisionErrorOccurred, self._on_vision_error)
        self._bus.subscribe(UserRecognized, self._on_user_recognized)
        self._bus.subscribe(PresenceLost, self._on_presence_lost)
        self._bus.subscribe(AutoLockTriggered, self._on_auto_lock)
        self._bus.subscribe(GestureRecognized, self._on_gesture)
        self._bus.subscribe(TelemetryUpdated, self._on_telemetry)
        self._bus.subscribe(AuditRecordWritten, self._on_audit_written)

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
        self._state.apply_approval_requested(
            request_id=event.request_id,
            action=event.action,
            summary=event.summary,
            tool_id=event.tool_id,
            risk_level=event.risk_level,
            target=event.target,
            arguments_summary=event.arguments_summary,
            agent_run_id=event.agent_run_id,
            timeout_seconds=event.timeout_seconds,
        )
        self._state.push_notification(
            "warning", f"Approval requested: {event.action or event.summary}"
        )

    def _on_approval_decided(self, event: ApprovalDecided) -> None:
        self._state.apply_approval_decided(
            event.request_id, event.approved, event.decided_by
        )
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

    # -- Agent activity (Phase 4.5) ---------------------------------------------

    def _on_agent_started(self, event: AgentRunStarted) -> None:
        self._state.apply_agent_run_started(
            event.run_id, event.agent_id, event.parent_run_id
        )

    def _on_agent_step(self, event: AgentStepUpdated) -> None:
        self._state.apply_agent_step_updated(
            event.run_id, event.agent_id, event.step, event.progress
        )

    def _on_agent_finished(self, event: AgentRunFinished) -> None:
        self._state.apply_agent_run_finished(
            event.run_id,
            event.agent_id,
            event.status,
            event.duration_ms,
            event.error_category,
        )

    # -- Voice & audio (Phase 5.5) ----------------------------------------------

    def _on_voice_state(self, event: VoiceStateChanged) -> None:
        self._state.apply_voice_state(
            event.state, event.mic_active, event.wake_word_enabled
        )

    def _on_audio_level(self, event: AudioLevelUpdated) -> None:
        self._state.apply_voice_level(event.direction, event.level)

    def _on_audio_device_connected(self, event: AudioDeviceConnected) -> None:
        self._state.apply_audio_device_connected(
            event.device_id, event.device_name, event.direction
        )

    def _on_audio_device_disconnected(self, event: AudioDeviceDisconnected) -> None:
        self._state.apply_audio_device_disconnected(
            event.device_id, event.device_name, event.direction
        )

    def _on_audio_error(self, event: AudioErrorOccurred) -> None:
        self._state.apply_voice_error(event.detail)

    def _on_voice_partial(self, event: VoiceTranscriptPartial) -> None:
        self._state.apply_voice_transcript_partial(event.text, event.confidence)

    def _on_voice_ready(self, event: VoiceTranscriptReady) -> None:
        self._state.apply_voice_transcript_ready(event.text, event.confidence)

    # -- Vision & biometrics (Phase 6.4) --------------------------------------

    def _on_vision_state(self, event: VisionStateChanged) -> None:
        self._state.apply_vision_state(
            event.state,
            event.camera_active,
            event.face_recognition_enabled,
            event.gesture_recognition_enabled,
        )

    def _on_camera_connected(self, event: CameraConnected) -> None:
        self._state.apply_camera_connected(event.device_id, event.device_name)

    def _on_camera_disconnected(self, event: CameraDisconnected) -> None:
        self._state.apply_camera_disconnected(event.device_id, event.device_name)

    def _on_vision_error(self, event: VisionErrorOccurred) -> None:
        self._state.apply_vision_error(event.detail)

    def _on_user_recognized(self, event: UserRecognized) -> None:
        self._state.apply_user_recognized(
            event.profile_id, event.display_name, event.confidence
        )

    def _on_presence_lost(self, event: PresenceLost) -> None:
        self._state.apply_presence_lost(event.elapsed_seconds)

    def _on_auto_lock(self, event: AutoLockTriggered) -> None:
        self._state.apply_auto_lock_triggered(event.reason)

    def _on_gesture(self, event: GestureRecognized) -> None:
        self._state.apply_gesture_recognized(
            event.gesture, event.action, event.confidence
        )

    # -- Security & telemetry (Phase 7.4) ------------------------------------

    def _on_telemetry(self, event: TelemetryUpdated) -> None:
        self._state.apply_telemetry(
            cpu_percent=event.cpu_percent,
            ram_percent=event.ram_percent,
            disk_percent=event.disk_percent,
            gpu_percent=event.gpu_percent,
        )

    def _on_audit_written(self, event: AuditRecordWritten) -> None:
        self._state.apply_audit_record_written(event.action, event.result)
