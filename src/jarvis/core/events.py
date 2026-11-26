"""Typed event contract for the JARVIS event bus.

Every event crossing module boundaries is a frozen dataclass subclassing
:class:`Event`. The class name (converted to snake case) is used as the
topic name, so subscribers can filter by type while the transport stays a
simple string-based pub/sub.
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Event:
    """Base event. Subclasses define the topic and payload fields."""

    timestamp: float = field(default_factory=time.monotonic, init=False)

    @property
    def name(self) -> str:
        """Topic name, e.g. ``service_status_changed``."""
        return _to_snake_case(type(self).__name__)

    def as_dict(self) -> dict[str, Any]:
        """Serialise the event payload (excluding the timestamp)."""
        return {
            k: v
            for k, v in dataclasses.asdict(self).items()
            if k != "timestamp"
        }


def _to_snake_case(name: str) -> str:
    out = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0:
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


# --- Lifecycle -------------------------------------------------------------


@dataclass(frozen=True)
class StartupCompleted(Event):
    """Core engine finished starting all services."""


@dataclass(frozen=True)
class ShutdownRequested(Event):
    """Graceful application shutdown was requested (tray → Quit)."""


@dataclass(frozen=True)
class ShutdownCompleted(Event):
    """Core engine finished shutting down."""


# --- Services --------------------------------------------------------------


@dataclass(frozen=True)
class ServiceStatusChanged(Event):
    """A registered service changed health/status."""

    service: str = ""
    status: str = "unknown"  # healthy | degraded | unavailable | starting | stopped
    detail: str = ""


# --- Security --------------------------------------------------------------


@dataclass(frozen=True)
class KillSwitchTriggered(Event):
    """Emergency stop engaged — all cancellable work must halt."""

    reason: str = ""


@dataclass(frozen=True)
class KillSwitchRearmed(Event):
    """Emergency stop disengaged; normal operation may resume."""


@dataclass(frozen=True)
class ApprovalRequested(Event):
    """A sensitive action awaits explicit user approval."""

    request_id: str = ""
    action: str = ""
    summary: str = ""
    tool_id: str = ""
    risk_level: str = ""
    target: str = ""
    arguments_summary: str = ""
    agent_run_id: str = ""
    fingerprint: str = ""
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class ApprovalDecided(Event):
    """An approval request was decided."""

    request_id: str = ""
    approved: bool = False
    decided_by: str = "default"  # user | timeout | fail_closed_default


@dataclass(frozen=True)
class AuditRecordWritten(Event):
    """An execution-audit record was appended."""

    action: str = ""
    result: str = ""


@dataclass(frozen=True)
class TelemetryUpdated(Event):
    """Local hardware telemetry snapshot (Phase 7.4, never sent to cloud)."""

    cpu_percent: float = 0.0
    ram_percent: float = 0.0
    disk_percent: float = 0.0
    gpu_percent: float | None = None


# --- Intelligence providers -------------------------------------------------


@dataclass(frozen=True)
class ProviderHealthChanged(Event):
    """An AI provider's availability changed."""

    provider: str = ""
    available: bool = False
    detail: str = ""


@dataclass(frozen=True)
class ProviderSelected(Event):
    """The router chose a provider/model for a request."""

    request_id: str = ""
    provider: str = ""
    model: str = ""
    privacy: str = ""  # local | cloud


# --- Chat streaming (Phase 2) ------------------------------------------------


@dataclass(frozen=True)
class ChatStreamStarted(Event):
    """A chat stream was accepted and started.

    ``privacy`` is additive metadata (``local`` | ``cloud``) forwarded from
    ``StreamRouted`` so every desktop mode can display where inference ran
    without inspecting provider internals.
    """

    request_id: str = ""
    provider: str = ""
    model: str = ""
    privacy: str = ""


@dataclass(frozen=True)
class ChatToken(Event):
    """One streamed token. ``seq`` is monotonic per request (deduped)."""

    request_id: str = ""
    seq: int = 0
    text: str = ""


@dataclass(frozen=True)
class ChatStreamCompleted(Event):
    """A chat stream finished successfully."""

    request_id: str = ""
    content: str = ""
    tokens_used: int = 0


@dataclass(frozen=True)
class ChatStreamFailed(Event):
    """A chat stream failed (partial content is preserved upstream)."""

    request_id: str = ""
    detail: str = ""  # human-readable; never contains credentials
    partial_content: str = ""  # exactly the tokens published before failure


@dataclass(frozen=True)
class ChatStreamCancelled(Event):
    """A chat stream was cancelled (Stop / kill switch); partial kept."""

    request_id: str = ""
    partial_content: str = ""


@dataclass(frozen=True)
class AssistantStateChanged(Event):
    """The aggregate assistant state changed (idle | thinking | speaking)."""

    state: str = "idle"


@dataclass(frozen=True)
class CloudFallbackProposed(Event):
    """Local inference failed; the UI should ask for cloud consent.

    Carries ONLY safe metadata — never credentials or hidden context.
    """

    request_id: str = ""
    reason: str = ""  # human-readable, credential-free
    provider: str = ""  # target cloud provider id
    model: str = ""  # target model ("" = provider default)
    payload_preview: str = ""  # exact outbound scope, human-safe
    payload_fingerprint: str = ""  # sha256 binding consent ↔ payload
    scope: str = ""  # scope description


@dataclass(frozen=True)
class CloudConsentDecided(Event):
    """A cloud consent decision was finalized (granted or denied)."""

    request_id: str = ""
    granted: bool = False
    decided_by: str = "fail_closed"  # user | timeout | fail_closed
    fingerprint: str = ""


@dataclass(frozen=True)
class AgentRunStarted(Event):
    """An agent run began execution (Phase 4)."""

    run_id: str = ""
    agent_id: str = ""
    parent_run_id: str = ""


@dataclass(frozen=True)
class AgentStepUpdated(Event):
    """An agent run reported step progress (Phase 4)."""

    run_id: str = ""
    agent_id: str = ""
    step: str = ""
    progress: float = 0.0


@dataclass(frozen=True)
class AgentRunFinished(Event):
    """An agent run reached a terminal status (Phase 4)."""

    run_id: str = ""
    agent_id: str = ""
    status: str = ""
    duration_ms: int = 0
    error_category: str = ""


@dataclass(frozen=True)
class AudioDeviceConnected(Event):
    """An audio input/output device became available (Phase 5.1)."""

    device_id: str = ""
    device_name: str = ""
    direction: str = "input"  # input | output


@dataclass(frozen=True)
class AudioDeviceDisconnected(Event):
    """An audio input/output device was removed (Phase 5.1)."""

    device_id: str = ""
    device_name: str = ""
    direction: str = "input"


@dataclass(frozen=True)
class AudioErrorOccurred(Event):
    """A non-fatal audio subsystem error occurred (Phase 5.1)."""

    detail: str = ""


@dataclass(frozen=True)
class AudioLevelUpdated(Event):
    """Smoothed/normalized audio RMS level for UI orb reactivity (Phase 5)."""

    level: float = 0.0
    direction: str = "input"  # input | output


@dataclass(frozen=True)
class VoiceTranscriptPartial(Event):
    """Intermediate streaming STT transcript (never submits chat)."""

    text: str = ""
    confidence: float = 0.0


@dataclass(frozen=True)
class VoiceTranscriptReady(Event):
    """Finalized local STT transcript eligible for ChatController.send()."""

    text: str = ""
    language: str = ""
    confidence: float = 0.0


@dataclass(frozen=True)
class VoiceStateChanged(Event):
    """Voice subsystem state and microphone privacy indicator changed."""

    state: str = "disabled"
    mic_active: bool = False
    wake_word_enabled: bool = False


@dataclass(frozen=True)
class WakeWordDetected(Event):
    """Offline wake word triggered listening."""

    phrase: str = ""
    score: float = 0.0


@dataclass(frozen=True)
class CameraConnected(Event):
    """A camera device became available (Phase 6.1)."""

    device_id: str = ""
    device_name: str = ""


@dataclass(frozen=True)
class CameraDisconnected(Event):
    """A camera device was removed or lost (Phase 6.1)."""

    device_id: str = ""
    device_name: str = ""


@dataclass(frozen=True)
class VisionErrorOccurred(Event):
    """A non-fatal vision/camera subsystem error occurred (Phase 6.1)."""

    detail: str = ""


@dataclass(frozen=True)
class VisionStateChanged(Event):
    """Vision subsystem state and camera-active indicator changed (Phase 6.1)."""

    state: str = "disabled"
    camera_active: bool = False
    face_recognition_enabled: bool = False
    gesture_recognition_enabled: bool = False


@dataclass(frozen=True)
class UserRecognized(Event):
    """Authorized local biometric profile recognized (greeting signal only)."""

    profile_id: str = ""
    display_name: str = ""
    confidence: float = 0.0
    recognized_at: float = 0.0


@dataclass(frozen=True)
class PresenceLost(Event):
    """Previously present user is no longer detected."""

    last_seen_at: float = 0.0
    elapsed_seconds: float = 0.0


@dataclass(frozen=True)
class AutoLockTriggered(Event):
    """Vision auto-lock engaged after sustained absence."""

    reason: str = "presence_lost"


@dataclass(frozen=True)
class GestureRecognized(Event):
    """Debounced hand gesture recognized (UI controller enforces permissions)."""

    gesture: str = ""
    action: str = ""
    confidence: float = 0.0
