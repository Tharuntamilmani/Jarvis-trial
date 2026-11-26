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

    # Agent activity (Phase 4.5) — forwarded by the EventBridge.
    agent_run_started = Signal(str, str, str)               # run_id, agent_id, parent_run_id
    agent_step_updated = Signal(str, str, str, float)       # run_id, agent_id, step, progress
    agent_run_finished = Signal(str, str, str, int, str)    # run_id, agent_id, status, ms, err

    # Voice & audio (Phase 5.5) — forwarded by the EventBridge / VoiceController.
    voice_state_changed = Signal(str, bool, bool)           # state, mic_active, wake_word_enabled
    voice_levels_updated = Signal(float, float)             # input_level, output_level
    voice_devices_changed = Signal(list)                    # list[dict]
    voice_error_changed = Signal(str)                       # detail (empty = healthy)
    voice_transcript_partial = Signal(str, float)           # text, confidence
    voice_transcript_ready = Signal(str, float)             # text, confidence
    voice_muted_changed = Signal(bool)
    voice_ptt_changed = Signal(bool)

    # Vision & biometrics (Phase 6.4) — forwarded by EventBridge / VisionController.
    vision_state_changed = Signal(str, bool, bool, bool)    # state, camera_active, face, gesture
    vision_devices_changed = Signal(list)                   # list[dict]
    vision_error_changed = Signal(str)                      # detail
    vision_user_recognized = Signal(str, str, float)        # profile_id, display_name, confidence
    vision_presence_lost = Signal(float)                    # elapsed_seconds
    vision_auto_lock_triggered = Signal(str)                # reason
    vision_gesture_recognized = Signal(str, str, float)     # gesture, action, confidence

    # Tool approval (Phase 7.2) — forwarded by EventBridge.
    approval_requested = Signal(dict)                       # request payload
    approval_decided = Signal(str, bool, str)               # request_id, approved, decided_by

    # Security & telemetry (Phase 7.4) — forwarded by EventBridge.
    telemetry_updated = Signal(dict)                        # cpu/ram/disk/gpu snapshot
    audit_record_written = Signal(str, str)                 # action, result

    def __init__(self) -> None:
        super().__init__()
        self.engine_state: str = "stopped"
        self.service_statuses: dict[str, str] = {}
        self.service_details: dict[str, str] = {}
        self.provider_health: dict[str, dict[str, Any]] = {}
        self.kill_switch_active: bool = False
        self.assistant_state: str = "idle"
        self.active_agents: dict[str, dict[str, Any]] = {}
        self.recent_agent_activity: list[dict[str, Any]] = []
        self.voice_state: str = "disabled"
        self.voice_mic_active: bool = False
        self.voice_wake_word_enabled: bool = False
        self.voice_muted: bool = False
        self.voice_ptt_active: bool = False
        self.voice_input_level: float = 0.0
        self.voice_output_level: float = 0.0
        self.voice_devices: list[dict[str, Any]] = []
        self.voice_selected_input_device: str | None = None
        self.voice_mic_available: bool = True
        self.voice_error_detail: str = ""
        self.voice_partial_transcript: str = ""
        self.vision_state: str = "disabled"
        self.vision_camera_active: bool = False
        self.vision_face_enabled: bool = False
        self.vision_gesture_enabled: bool = False
        self.vision_devices: list[dict[str, Any]] = []
        self.vision_selected_device: str | None = None
        self.vision_camera_available: bool = True
        self.vision_error_detail: str = ""
        self.vision_last_user: str = ""
        self.telemetry: dict[str, Any] = {
            "cpu_percent": 0.0,
            "ram_percent": 0.0,
            "disk_percent": 0.0,
            "gpu_percent": None,
        }

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

    # -- Agent mutators (called by the bridge only) ------------------------------

    def apply_agent_run_started(
        self, run_id: str, agent_id: str, parent_run_id: str = ""
    ) -> None:
        self.active_agents[run_id] = {
            "run_id": run_id,
            "agent_id": agent_id,
            "parent_run_id": parent_run_id,
            "status": "running",
            "step": "starting",
            "progress": 0.0,
        }
        self.agent_run_started.emit(run_id, agent_id, parent_run_id)

    def apply_agent_step_updated(
        self, run_id: str, agent_id: str, step: str, progress: float = 0.0
    ) -> None:
        entry = self.active_agents.get(run_id)
        if entry is not None:
            entry["step"] = step
            entry["progress"] = progress
        self.agent_step_updated.emit(run_id, agent_id, step, progress)

    def apply_agent_run_finished(
        self,
        run_id: str,
        agent_id: str,
        status: str,
        duration_ms: int = 0,
        error_category: str = "",
    ) -> None:
        self.active_agents.pop(run_id, None)
        self.recent_agent_activity.insert(
            0,
            {
                "run_id": run_id,
                "agent_id": agent_id,
                "status": status,
                "duration_ms": duration_ms,
                "error_category": error_category,
            },
        )
        del self.recent_agent_activity[20:]
        self.agent_run_finished.emit(
            run_id, agent_id, status, duration_ms, error_category
        )

    # -- Voice & audio mutators (Phase 5.5) -------------------------------------

    def apply_voice_state(
        self, state: str, mic_active: bool, wake_word_enabled: bool
    ) -> None:
        self.voice_state = state
        self.voice_mic_active = mic_active
        self.voice_wake_word_enabled = wake_word_enabled
        if state in ("listening", "processing", "speaking"):
            self.voice_mic_available = True
            if self.voice_error_detail:
                self.voice_error_detail = ""
                self.voice_error_changed.emit("")
        self.voice_state_changed.emit(state, mic_active, wake_word_enabled)

    def apply_voice_level(self, direction: str, level: float) -> None:
        clamped = max(0.0, min(1.0, float(level)))
        if direction == "output":
            self.voice_output_level = clamped
        else:
            self.voice_input_level = clamped
        self.voice_levels_updated.emit(
            self.voice_input_level, self.voice_output_level
        )

    def apply_voice_devices(self, devices: list[dict[str, Any]]) -> None:
        self.voice_devices = [dict(d) for d in devices]
        has_input = any(d.get("is_input") for d in self.voice_devices)
        self.voice_mic_available = has_input
        if not has_input and not self.voice_error_detail:
            self.voice_error_detail = "No microphone available"
            self.voice_error_changed.emit(self.voice_error_detail)
        elif has_input and self.voice_error_detail == "No microphone available":
            self.voice_error_detail = ""
            self.voice_error_changed.emit("")
        self.voice_devices_changed.emit(list(self.voice_devices))

    def apply_audio_device_connected(
        self, device_id: str, device_name: str, direction: str
    ) -> None:
        already_known = any(d.get("device_id") == device_id for d in self.voice_devices)
        entry = {
            "device_id": device_id,
            "device_name": device_name,
            "is_input": direction == "input",
            "is_output": direction == "output",
            "is_default": False,
        }
        updated = [d for d in self.voice_devices if d.get("device_id") != device_id]
        updated.append(entry)
        self.apply_voice_devices(updated)
        if not already_known and len(updated) == 1:
            # Avoid flooding notifications during initial multi-device enumeration.
            return
        if not already_known:
            self.push_notification(
                "info", f"Audio device connected: {device_name or device_id}"
            )

    def apply_audio_device_disconnected(
        self, device_id: str, device_name: str, direction: str
    ) -> None:
        del direction
        updated = [d for d in self.voice_devices if d.get("device_id") != device_id]
        self.apply_voice_devices(updated)
        self.push_notification(
            "warning", f"Audio device disconnected: {device_name or device_id}"
        )

    def apply_voice_error(self, detail: str) -> None:
        self.voice_error_detail = detail
        if detail:
            self.voice_mic_available = False
        self.voice_error_changed.emit(detail)
        if detail:
            self.push_notification("warning", f"Audio: {detail}")

    def apply_voice_transcript_partial(self, text: str, confidence: float) -> None:
        self.voice_partial_transcript = text
        self.voice_transcript_partial.emit(text, confidence)

    def apply_voice_transcript_ready(self, text: str, confidence: float) -> None:
        self.voice_partial_transcript = ""
        self.voice_transcript_ready.emit(text, confidence)

    def set_voice_muted(self, muted: bool) -> None:
        self.voice_muted = bool(muted)
        self.voice_muted_changed.emit(self.voice_muted)

    def set_voice_ptt(self, active: bool) -> None:
        self.voice_ptt_active = bool(active)
        self.voice_ptt_changed.emit(self.voice_ptt_active)

    def set_voice_selected_input_device(self, device_id: str | None) -> None:
        self.voice_selected_input_device = device_id

    # -- Vision & biometrics mutators (Phase 6.4) -----------------------------

    def apply_vision_state(
        self,
        state: str,
        camera_active: bool,
        face_enabled: bool,
        gesture_enabled: bool,
    ) -> None:
        self.vision_state = state
        self.vision_camera_active = camera_active
        self.vision_face_enabled = face_enabled
        self.vision_gesture_enabled = gesture_enabled
        if camera_active:
            self.vision_camera_available = True
            if self.vision_error_detail:
                self.vision_error_detail = ""
                self.vision_error_changed.emit("")
        self.vision_state_changed.emit(
            state, camera_active, face_enabled, gesture_enabled
        )

    def apply_vision_devices(self, devices: list[dict[str, Any]]) -> None:
        self.vision_devices = [dict(d) for d in devices]
        self.vision_camera_available = bool(self.vision_devices)
        if not self.vision_camera_available and not self.vision_error_detail:
            self.vision_error_detail = "No camera available"
            self.vision_error_changed.emit(self.vision_error_detail)
        elif self.vision_camera_available and self.vision_error_detail == "No camera available":
            self.vision_error_detail = ""
            self.vision_error_changed.emit("")
        self.vision_devices_changed.emit(list(self.vision_devices))

    def apply_camera_connected(self, device_id: str, device_name: str) -> None:
        updated = [d for d in self.vision_devices if d.get("device_id") != device_id]
        updated.append({"device_id": device_id, "device_name": device_name})
        self.apply_vision_devices(updated)
        self.push_notification("info", f"Camera connected: {device_name or device_id}")

    def apply_camera_disconnected(self, device_id: str, device_name: str) -> None:
        updated = [d for d in self.vision_devices if d.get("device_id") != device_id]
        self.apply_vision_devices(updated)
        self.push_notification(
            "warning", f"Camera disconnected: {device_name or device_id}"
        )

    def apply_vision_error(self, detail: str) -> None:
        self.vision_error_detail = detail
        if detail:
            self.vision_camera_available = False
        self.vision_error_changed.emit(detail)
        if detail:
            self.push_notification("warning", f"Vision: {detail}")

    def apply_user_recognized(
        self, profile_id: str, display_name: str, confidence: float
    ) -> None:
        self.vision_last_user = display_name or profile_id
        self.vision_user_recognized.emit(profile_id, display_name, confidence)

    def apply_presence_lost(self, elapsed_seconds: float) -> None:
        self.vision_presence_lost.emit(elapsed_seconds)

    def apply_auto_lock_triggered(self, reason: str) -> None:
        self.vision_auto_lock_triggered.emit(reason)
        self.push_notification("info", f"Vision auto-lock engaged ({reason})")

    def apply_gesture_recognized(
        self, gesture: str, action: str, confidence: float
    ) -> None:
        self.vision_gesture_recognized.emit(gesture, action, confidence)

    def set_vision_selected_device(self, device_id: str | None) -> None:
        self.vision_selected_device = device_id

    # -- Tool approval mutators (Phase 7.2) -----------------------------------

    def apply_approval_requested(
        self,
        *,
        request_id: str,
        action: str,
        summary: str = "",
        tool_id: str = "",
        risk_level: str = "",
        target: str = "",
        arguments_summary: str = "",
        agent_run_id: str = "",
        timeout_seconds: float = 30.0,
    ) -> None:
        self.approval_requested.emit(
            {
                "request_id": request_id,
                "action": action,
                "summary": summary,
                "tool_id": tool_id or action,
                "risk_level": risk_level,
                "target": target,
                "arguments_summary": arguments_summary,
                "agent_run_id": agent_run_id,
                "timeout_seconds": timeout_seconds,
            }
        )

    def apply_approval_decided(
        self, request_id: str, approved: bool, decided_by: str
    ) -> None:
        self.approval_decided.emit(request_id, approved, decided_by)

    # -- Security & telemetry mutators (Phase 7.4) ---------------------------

    def apply_telemetry(
        self,
        *,
        cpu_percent: float,
        ram_percent: float,
        disk_percent: float,
        gpu_percent: float | None = None,
    ) -> None:
        self.telemetry = {
            "cpu_percent": cpu_percent,
            "ram_percent": ram_percent,
            "disk_percent": disk_percent,
            "gpu_percent": gpu_percent,
        }
        self.telemetry_updated.emit(dict(self.telemetry))

    def apply_audit_record_written(self, action: str, result: str) -> None:
        self.audit_record_written.emit(action, result)

    # -- Snapshot helpers ----------------------------------------------------------

    def status_snapshot(self) -> dict[str, str]:
        return dict(self.service_statuses)

    def provider_snapshot(self) -> dict[str, dict[str, Any]]:
        return {
            name: dict(info) for name, info in self.provider_health.items()
        }
