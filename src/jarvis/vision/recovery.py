"""Vision recovery manager: stall detection, bounded reconnect, never auto-enables."""

from __future__ import annotations

from dataclasses import dataclass
import time

from jarvis.core.event_bus import EventBus
from jarvis.core.events import VisionErrorOccurred
from jarvis.vision.device_manager import CameraDeviceManager
from jarvis.vision.diagnostics import VisionDiagnostics
from jarvis.vision.models import CameraDeviceInfo


@dataclass(frozen=True)
class VisionRecoveryConfig:
    """Thresholds for camera capture stall detection and bounded recovery."""

    capture_stall_seconds: float = 5.0
    max_recovery_attempts: int = 3


class VisionRecoveryManager:
    """Recovers camera availability after faults without overriding user consent."""

    def __init__(
        self,
        bus: EventBus,
        diagnostics: VisionDiagnostics,
        config: VisionRecoveryConfig | None = None,
    ) -> None:
        self._bus = bus
        self._diag = diagnostics
        self._config = config or VisionRecoveryConfig()
        self._attempts = 0

    def is_capture_stalled(
        self, last_frame_at: float | None, *, now: float | None = None
    ) -> bool:
        if last_frame_at is None:
            return False
        current = time.monotonic() if now is None else now
        return (current - last_frame_at) > self._config.capture_stall_seconds

    def reset_attempts(self) -> None:
        self._attempts = 0

    async def recover_device_failure(
        self,
        device_manager: CameraDeviceManager,
        detail: str,
        *,
        category: str = "camera_error",
    ) -> tuple[CameraDeviceInfo, ...]:
        """Emit error, probe devices up to bounded attempts; never restarts capture."""
        self._diag.record_error(category)
        await self._bus.publish(VisionErrorOccurred(detail=detail))
        if self._attempts >= self._config.max_recovery_attempts:
            return device_manager.devices
        self._attempts += 1
        self._diag.record_recovery_attempt()
        return await device_manager.refresh_devices()
