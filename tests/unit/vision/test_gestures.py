"""Unit tests for Phase 6.3 MediaPipeGestureEngine & GestureTracker."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from jarvis.vision import (
    CameraFrame,
    GestureConfig,
    GestureDetection,
    GestureTracker,
    MediaPipeGestureEngine,
    VisionError,
)


class _FakeGestureEngine:
    def __init__(self, detections: list[GestureDetection]) -> None:
        self.detections = detections

    def detect_gesture(self, frame: CameraFrame) -> GestureDetection | None:
        del frame
        return self.detections.pop(0) if self.detections else None


def _frame(ts: float = 1.0) -> CameraFrame:
    return CameraFrame(data=b"\x00" * 12, width=2, height=2, channels=3, timestamp=ts)


def test_mediapipe_missing_dependency_raises_clean_vision_error() -> None:
    engine = MediaPipeGestureEngine()
    with patch.dict(sys.modules, {"mediapipe": None}):
        with pytest.raises(VisionError, match="mediapipe"):
            engine.detect_gesture(_frame())


def test_gesture_tracker_debounce_cooldown_and_unknown_rejection() -> None:
    engine = _FakeGestureEngine(
        [
            GestureDetection(gesture="unknown_wave", confidence=0.95),
            GestureDetection(gesture="open_palm", confidence=0.40),  # below threshold
            GestureDetection(gesture="open_palm", confidence=0.90),  # 1/2 debounce
            GestureDetection(gesture="open_palm", confidence=0.92),  # 2/2 -> fires!
            GestureDetection(gesture="open_palm", confidence=0.95),  # cooled down
            GestureDetection(gesture="open_palm", confidence=0.95),  # cooled down
        ]
    )
    tracker = GestureTracker(
        engine,
        GestureConfig(
            min_confidence=0.70,
            debounce_frames=2,
            cooldown_seconds=5.0,
        ),
    )

    assert tracker.process(_frame(1.0), now=1.0) is None  # unknown rejected
    assert tracker.process(_frame(1.1), now=1.1) is None  # low confidence rejected
    assert tracker.process(_frame(1.2), now=1.2) is None  # 1st frame only
    res = tracker.process(_frame(1.3), now=1.3)
    assert res is not None
    assert res[0] == "open_palm"
    assert res[1] == "toggle_hud"

    # Cooldown prevents immediate re-trigger
    assert tracker.process(_frame(2.0), now=2.0) is None
    assert tracker.process(_frame(2.1), now=2.1) is None
