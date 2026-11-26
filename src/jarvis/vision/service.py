"""VisionService: orchestrates CameraIO, Face, Gestures, Presence & KillSwitch (Phase 6.3)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import time
from typing import TYPE_CHECKING, Any

from jarvis.core.event_bus import EventBus
from jarvis.core.events import (
    AutoLockTriggered,
    GestureRecognized,
    KillSwitchTriggered,
    PresenceLost,
    UserRecognized,
    VisionErrorOccurred,
    VisionStateChanged,
)
from jarvis.security.biometrics import BiometricProfile, BiometricStore
from jarvis.vision.camera import CameraIO
from jarvis.vision.device_manager import CameraDeviceManager
from jarvis.vision.diagnostics import VisionDiagnostics
from jarvis.vision.face import LocalFaceMatcher
from jarvis.vision.gestures import GestureTracker
from jarvis.vision.models import (
    CameraConfig,
    CameraDeviceInfo,
    CameraFrame,
    VisionError,
    VisionState,
    VisionStateMachine,
)
from jarvis.vision.recovery import VisionRecoveryManager

if TYPE_CHECKING:  # pragma: no cover - typing only
    from jarvis.core.lifecycle import LifecycleManager


@dataclass(frozen=True)
class PresenceConfig:
    """Configuration for presence detection and auto-lock."""

    absence_timeout_seconds: float = 60.0
    consecutive_misses_required: int = 3
    auto_lock_enabled: bool = True


class VisionService:
    """Local-first vision orchestrator (zero PySide6, zero cloud)."""

    def __init__(
        self,
        *,
        camera_io: CameraIO,
        device_manager: CameraDeviceManager,
        bus: EventBus,
        biometrics: BiometricStore | None = None,
        face_matcher: LocalFaceMatcher | None = None,
        gesture_tracker: GestureTracker | None = None,
        lifecycle: LifecycleManager | None = None,
        presence_config: PresenceConfig | None = None,
        diagnostics: VisionDiagnostics | None = None,
        recovery: VisionRecoveryManager | None = None,
    ) -> None:
        self._io = camera_io
        self._devices = device_manager
        self._bus = bus
        self._biometrics = biometrics or BiometricStore()
        self._face = face_matcher
        self._gestures = gesture_tracker
        self._lifecycle = lifecycle
        self._presence_cfg = presence_config or PresenceConfig()
        self._diagnostics = diagnostics or VisionDiagnostics()
        self._recovery = recovery or VisionRecoveryManager(bus, self._diagnostics)

        self._sm = VisionStateMachine(VisionState.DISABLED)
        self._active_handle_id: str | None = None
        self._face_enabled = False
        self._gesture_enabled = False
        self._last_seen_at: float | None = None
        self._consecutive_misses = 0
        self._presence_lost_emitted = False

        self._bus.subscribe(KillSwitchTriggered, self._on_kill_switch)

    def get_state(self) -> VisionState:
        return self._sm.state

    @property
    def camera_active(self) -> bool:
        return self._sm.camera_active

    @property
    def face_recognition_enabled(self) -> bool:
        return self._face_enabled and not self._kill_switch_active()

    @property
    def gesture_recognition_enabled(self) -> bool:
        return self._gesture_enabled and not self._kill_switch_active()

    def _kill_switch_active(self) -> bool:
        return self._lifecycle is not None and self._lifecycle.kill_switch_active

    async def refresh_devices(self) -> tuple[CameraDeviceInfo, ...]:
        return await self._devices.refresh_devices()

    def select_device(self, device_id: str | None) -> None:
        self._devices.select_device(device_id)

    async def enable(self, config: CameraConfig | None = None) -> VisionState:
        """Explicitly enable and start local camera capture."""
        if self._kill_switch_active():
            return self._sm.state
        cfg = config or CameraConfig(device_id=self._devices.preferred_device_id)
        if self._sm.state in (VisionState.DISABLED, VisionState.ERROR, VisionState.LOCKED):
            self._sm.transition(VisionState.IDLE)
        try:
            self._io.start(cfg)
        except VisionError as exc:
            self._diagnostics.record_error("camera_start_failed")
            self._sm.transition(VisionState.ERROR)
            await self._publish_state()
            await self._bus.publish(VisionErrorOccurred(detail=str(exc)))
            return self._sm.state

        handle_id = cfg.device_id or "default"
        self._active_handle_id = handle_id
        self._diagnostics.mark_handle_opened(handle_id)
        self._diagnostics.record_fps(cfg.max_fps)
        self._recovery.reset_attempts()
        self._last_seen_at = None
        self._consecutive_misses = 0
        self._presence_lost_emitted = False
        self._sm.transition(VisionState.ACTIVE)
        await self._publish_state()
        return self._sm.state

    async def disable(self) -> VisionState:
        """Stop camera capture, clear buffers, and transition to DISABLED."""
        self._io.stop()
        if self._active_handle_id is not None:
            self._diagnostics.mark_handle_closed(self._active_handle_id)
            self._active_handle_id = None
        if self._gestures is not None:
            self._gestures.reset()
        self._last_seen_at = None
        self._consecutive_misses = 0
        self._presence_lost_emitted = False
        self._sm.transition(VisionState.DISABLED)
        await self._publish_state()
        return self._sm.state

    async def set_face_recognition_enabled(self, enabled: bool) -> None:
        self._face_enabled = bool(enabled)
        await self._publish_state()

    async def set_gesture_recognition_enabled(self, enabled: bool) -> None:
        self._gesture_enabled = bool(enabled)
        if not self._gesture_enabled and self._gestures is not None:
            self._gestures.reset()
        await self._publish_state()

    def enroll_profile(
        self,
        profile_id: str,
        display_name: str,
        embedding: Sequence[float],
        *,
        consent_granted: bool = False,
    ) -> BiometricProfile:
        return self._biometrics.enroll_profile(
            profile_id,
            display_name,
            embedding,
            consent_granted=consent_granted,
        )

    def delete_profile(self, profile_id: str) -> bool:
        return self._biometrics.delete_profile(profile_id)

    async def process_frame(
        self, frame: CameraFrame, *, now: float | None = None
    ) -> None:
        """Process one in-memory frame when ACTIVE (never runs if DISABLED/LOCKED)."""
        if self._kill_switch_active():
            await self.disable()
            return
        if self._sm.state is not VisionState.ACTIVE:
            return
        current = time.monotonic() if now is None else now

        if self._face_enabled and self._face is not None:
            try:
                match = self._face.identify(frame, now=current)
            except VisionError as exc:
                await self._bus.publish(VisionErrorOccurred(detail=str(exc)))
                match = None

            if match is not None:
                self._last_seen_at = current
                self._consecutive_misses = 0
                self._presence_lost_emitted = False
                await self._bus.publish(
                    UserRecognized(
                        profile_id=match.profile_id,
                        display_name=match.display_name,
                        confidence=match.similarity,
                        recognized_at=current,
                    )
                )
            elif self._last_seen_at is not None:
                self._consecutive_misses += 1
                elapsed = current - self._last_seen_at
                if (
                    not self._presence_lost_emitted
                    and self._consecutive_misses >= self._presence_cfg.consecutive_misses_required
                    and elapsed >= self._presence_cfg.absence_timeout_seconds
                ):
                    self._presence_lost_emitted = True
                    await self._bus.publish(
                        PresenceLost(
                            last_seen_at=self._last_seen_at,
                            elapsed_seconds=elapsed,
                        )
                    )
                    if self._presence_cfg.auto_lock_enabled:
                        self._io.stop()
                        self._sm.transition(VisionState.LOCKED)
                        await self._bus.publish(
                            AutoLockTriggered(reason="presence_lost")
                        )
                        await self._publish_state()
                        return

        if self._gesture_enabled and self._gestures is not None:
            try:
                gest = self._gestures.process(frame, now=current)
            except VisionError as exc:
                await self._bus.publish(VisionErrorOccurred(detail=str(exc)))
                gest = None
            if gest is not None:
                gesture, action, confidence = gest
                await self._bus.publish(
                    GestureRecognized(
                        gesture=gesture,
                        action=action,
                        confidence=confidence,
                    )
                )

    async def _publish_state(self) -> None:
        await self._bus.publish(
            VisionStateChanged(
                state=self._sm.state.value,
                camera_active=self._sm.camera_active,
                face_recognition_enabled=self.face_recognition_enabled,
                gesture_recognition_enabled=self.gesture_recognition_enabled,
            )
        )

    async def _on_kill_switch(self, event: KillSwitchTriggered) -> None:
        del event
        self._face_enabled = False
        self._gesture_enabled = False
        await self.disable()

    def diagnostics_snapshot(self) -> dict[str, Any]:
        """Return privacy-safe vision diagnostics (zero frames or embeddings)."""
        health = self._devices.health_snapshot()
        return self._diagnostics.snapshot(
            state=self._sm.state,
            camera_available=health["available_count"] > 0 or self._io.is_open,
            disconnect_count=int(health["disconnect_count"]),
        )

    async def recover_from_camera_error(
        self, detail: str
    ) -> tuple[CameraDeviceInfo, ...]:
        """Release camera handle, disable capture, and probe availability."""
        await self.disable()
        return await self._recovery.recover_device_failure(self._devices, detail)

    async def emergency_shutdown(self) -> None:
        """Immediately halt capture, clear buffers, release all handles."""
        self._face_enabled = False
        self._gesture_enabled = False
        await self.disable()
        self._diagnostics.clear_handles()
        self._io.close()

    async def close(self) -> None:
        await self.disable()
        self._diagnostics.clear_handles()
        self._io.close()
