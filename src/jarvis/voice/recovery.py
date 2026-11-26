"""Voice subsystem fault recovery, stuck-state detection, and emergency stop."""

from __future__ import annotations

import time
from dataclasses import dataclass

from jarvis.core.event_bus import EventBus
from jarvis.core.events import AudioErrorOccurred
from jarvis.voice.device_manager import AudioDeviceManager
from jarvis.voice.diagnostics import VoiceDiagnostics
from jarvis.voice.models import AudioDeviceInfo, VoiceState


@dataclass(frozen=True)
class RecoveryConfig:
    """Timeout thresholds (seconds) for stuck-state detection."""

    max_listening_seconds: float = 30.0
    max_processing_seconds: float = 25.0
    max_speaking_seconds: float = 60.0


class VoiceRecoveryManager:
    """Detects stuck voice states, recovers from device loss, and halts safely."""

    def __init__(
        self,
        bus: EventBus,
        diagnostics: VoiceDiagnostics,
        config: RecoveryConfig | None = None,
    ) -> None:
        self._bus = bus
        self._diag = diagnostics
        self._config = config or RecoveryConfig()

    def is_state_stuck(
        self, state: VoiceState, entered_at: float, *, now: float | None = None
    ) -> bool:
        """Return True if a transient voice state exceeded its maximum duration."""
        current = time.monotonic() if now is None else now
        elapsed = max(0.0, current - entered_at)
        if state is VoiceState.LISTENING:
            return elapsed > self._config.max_listening_seconds
        if state is VoiceState.PROCESSING:
            return elapsed > self._config.max_processing_seconds
        if state is VoiceState.SPEAKING:
            return elapsed > self._config.max_speaking_seconds
        return False

    async def recover_device_failure(
        self,
        device_manager: AudioDeviceManager,
        detail: str,
    ) -> tuple[AudioDeviceInfo, ...]:
        """Record device error, notify UI, and probe for replacement devices."""
        self._diag.record_error()
        self._diag.record_recovery()
        await self._bus.publish(AudioErrorOccurred(detail=detail))
        return await device_manager.refresh_devices()

    async def report_stuck_state(self, state: VoiceState) -> None:
        """Record stuck-state recovery and emit a non-fatal audio diagnostic event."""
        self._diag.record_error()
        self._diag.record_recovery()
        await self._bus.publish(
            AudioErrorOccurred(
                detail=f"Recovered from stuck voice state: {state.value}"
            )
        )
