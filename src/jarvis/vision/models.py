"""Core vision & camera value types and state machine (Phase 6.1, zero PySide6)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import time

from jarvis.core.exceptions import JarvisError


class VisionError(JarvisError):
    """Base error for vision, camera, and local perception."""


class CameraUnavailableError(VisionError):
    """Requested camera hardware or driver is unavailable."""


class InvalidVisionTransitionError(VisionError):
    """Raised when an invalid vision state transition is attempted."""


class VisionState(StrEnum):
    """High-level state of the vision subsystem."""

    DISABLED = "disabled"
    IDLE = "idle"
    ACTIVE = "active"
    LOCKED = "locked"
    ERROR = "error"


_ALLOWED_TRANSITIONS: dict[VisionState, frozenset[VisionState]] = {
    VisionState.DISABLED: frozenset({VisionState.IDLE, VisionState.ERROR}),
    VisionState.IDLE: frozenset(
        {VisionState.ACTIVE, VisionState.DISABLED, VisionState.LOCKED, VisionState.ERROR}
    ),
    VisionState.ACTIVE: frozenset(
        {VisionState.IDLE, VisionState.DISABLED, VisionState.LOCKED, VisionState.ERROR}
    ),
    VisionState.LOCKED: frozenset(
        {VisionState.IDLE, VisionState.DISABLED, VisionState.ERROR}
    ),
    VisionState.ERROR: frozenset({VisionState.DISABLED, VisionState.IDLE}),
}


class VisionStateMachine:
    """Enforces explicit opt-in and valid vision state transitions."""

    def __init__(self, initial: VisionState = VisionState.DISABLED) -> None:
        self._state = initial

    @property
    def state(self) -> VisionState:
        return self._state

    @property
    def camera_active(self) -> bool:
        return self._state is VisionState.ACTIVE

    def can_transition(self, target: VisionState) -> bool:
        if target is self._state:
            return True
        return target in _ALLOWED_TRANSITIONS[self._state]

    def transition(self, target: VisionState) -> VisionState:
        if target is self._state:
            return self._state
        if not self.can_transition(target):
            raise InvalidVisionTransitionError(
                f"Cannot transition vision state from {self._state.value!r} to {target.value!r}"
            )
        self._state = target
        return self._state


@dataclass(frozen=True)
class CameraDeviceInfo:
    """Metadata for one system camera device."""

    device_id: str
    device_name: str
    index: int = 0
    max_width: int = 1280
    max_height: int = 720
    is_default: bool = False


@dataclass(frozen=True)
class CameraConfig:
    """Normalized camera capture configuration."""

    device_id: str | None = None
    width: int = 640
    height: int = 480
    max_fps: float = 15.0
    capture_timeout_seconds: float = 2.0
    buffer_frames: int = 4
    max_frame_age_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Camera resolution must be positive")
        if self.max_fps <= 0:
            raise ValueError("max_fps must be positive")
        if self.buffer_frames <= 0:
            raise ValueError("buffer_frames must be positive")


@dataclass(frozen=True)
class CameraFrame:
    """One in-memory camera frame (never persisted to disk by default)."""

    data: bytes
    width: int
    height: int
    channels: int = 3
    timestamp: float = field(default_factory=time.monotonic)
    frame_id: int = 0
    encoding: str = "bgr8"

    def is_stale(self, now: float | None = None, *, max_age_seconds: float = 1.0) -> bool:
        current = time.monotonic() if now is None else now
        return (current - self.timestamp) > max_age_seconds
