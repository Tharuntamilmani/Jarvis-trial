"""Local hand gesture recognition with debounce & cooldown (Phase 6.3)."""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Protocol, runtime_checkable

from jarvis.vision.models import CameraFrame, VisionError

DEFAULT_GESTURE_ACTIONS: dict[str, str] = {
    "open_palm": "toggle_hud",
    "two_fingers": "toggle_floating",
    "closed_fist": "stop_speaking",
}


@dataclass(frozen=True)
class GestureConfig:
    """Configuration for MediaPipe gesture recognition & debouncing."""

    min_confidence: float = 0.70
    debounce_frames: int = 2
    cooldown_seconds: float = 2.0
    gesture_actions: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_GESTURE_ACTIONS)
    )


@dataclass(frozen=True)
class GestureDetection:
    """Single detected hand gesture."""

    gesture: str
    confidence: float


@runtime_checkable
class GestureRecognizer(Protocol):
    """Local hand gesture recognizer contract."""

    def detect_gesture(self, frame: CameraFrame) -> GestureDetection | None: ...


class MediaPipeGestureEngine:
    """Lazy-loaded MediaPipe hand gesture recognizer (local-only)."""

    def __init__(self, config: GestureConfig | None = None) -> None:
        self._config = config or GestureConfig()
        self._hands: Any | None = None

    def _load_hands(self) -> Any:
        if self._hands is not None:
            return self._hands
        try:
            import mediapipe as mp  # type: ignore[import-not-found]
        except ImportError as exc:
            raise VisionError("mediapipe package is not installed") from exc
        self._hands = mp.solutions.hands.Hands(
            static_image_mode=False,
            max_num_hands=1,
            min_detection_confidence=self._config.min_confidence,
        )
        return self._hands

    def detect_gesture(self, frame: CameraFrame) -> GestureDetection | None:
        try:
            import numpy as np  # type: ignore[import-not-found]
        except ImportError as exc:
            raise VisionError("numpy package is not installed") from exc

        hands = self._load_hands()
        arr = np.frombuffer(frame.data, dtype=np.uint8).reshape(
            (frame.height, frame.width, frame.channels)
        )
        result = hands.process(arr)
        landmarks = getattr(result, "multi_hand_landmarks", None)
        if not landmarks:
            return None
        hand = landmarks[0].landmark
        extended = self._count_extended_fingers(hand)
        if extended >= 4:
            return GestureDetection(gesture="open_palm", confidence=0.85)
        if extended == 2:
            return GestureDetection(gesture="two_fingers", confidence=0.85)
        if extended == 0:
            return GestureDetection(gesture="closed_fist", confidence=0.85)
        return None

    @staticmethod
    def _count_extended_fingers(landmarks: Any) -> int:
        # Index (8 vs 6), Middle (12 vs 10), Ring (16 vs 14), Pinky (20 vs 18)
        pairs = ((8, 6), (12, 10), (16, 14), (20, 18))
        return sum(1 for tip, pip in pairs if landmarks[tip].y < landmarks[pip].y)


class GestureTracker:
    """Debounces gestures, rejects unknown ones, and enforces cooldowns."""

    def __init__(
        self,
        recognizer: GestureRecognizer,
        config: GestureConfig | None = None,
    ) -> None:
        self._recognizer = recognizer
        self._config = config or GestureConfig()
        self._pending_gesture: str | None = None
        self._streak = 0
        self._last_fired_at: dict[str, float] = {}

    def reset(self) -> None:
        self._pending_gesture = None
        self._streak = 0

    def process(
        self, frame: CameraFrame, *, now: float | None = None
    ) -> tuple[str, str, float] | None:
        """Return ``(gesture, action, confidence)`` when debounced & valid."""
        current = time.monotonic() if now is None else now
        detection = self._recognizer.detect_gesture(frame)
        if detection is None:
            self.reset()
            return None

        gesture = detection.gesture
        action = self._config.gesture_actions.get(gesture)
        if action is None or detection.confidence < self._config.min_confidence:
            self.reset()
            return None

        if gesture == self._pending_gesture:
            self._streak += 1
        else:
            self._pending_gesture = gesture
            self._streak = 1

        if self._streak < max(1, self._config.debounce_frames):
            return None

        last = self._last_fired_at.get(gesture, -1e9)
        if (current - last) < self._config.cooldown_seconds:
            return None

        self._last_fired_at[gesture] = current
        self._streak = 0
        return (gesture, action, detection.confidence)
