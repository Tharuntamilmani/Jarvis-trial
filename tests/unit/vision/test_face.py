"""Unit tests for Phase 6.2 InsightFaceRecognizer and LocalFaceMatcher."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from jarvis.security.biometrics import BiometricStore
from jarvis.vision import (
    CameraFrame,
    FaceConfig,
    FaceDetection,
    InsightFaceRecognizer,
    LocalFaceMatcher,
    VisionError,
)


class _FakeFaceRecognizer:
    def __init__(self, detections: list[FaceDetection]) -> None:
        self.detections = detections

    def detect_and_embed(self, frame: CameraFrame) -> list[FaceDetection]:
        del frame
        return list(self.detections)


def _frame() -> CameraFrame:
    return CameraFrame(data=b"\x00" * 12, width=2, height=2, channels=3, timestamp=1.0)


def test_insightface_missing_dependency_raises_clean_vision_error() -> None:
    rec = InsightFaceRecognizer()
    with patch.dict(sys.modules, {"insightface": None, "insightface.app": None}):
        with pytest.raises(VisionError, match="insightface"):
            rec.detect_and_embed(_frame())


def test_local_face_matcher_thresholds_liveness_and_cooldown(tmp_path: Path) -> None:
    store = BiometricStore(storage_dir=tmp_path)
    store.enroll_profile("user_1", "Tharun", [1.0, 0.0, 0.0], consent_granted=True)

    # Low liveness (spoof) is rejected
    spoof_rec = _FakeFaceRecognizer(
        [FaceDetection(embedding=(1.0, 0.0, 0.0), confidence=0.95, liveness_score=0.1)]
    )
    matcher = LocalFaceMatcher(
        spoof_rec,
        store,
        FaceConfig(min_liveness_score=0.5, recognition_cooldown_seconds=10.0),
    )
    assert matcher.identify(_frame(), now=10.0) is None

    # Valid face matches once, then respects cooldown
    real_rec = _FakeFaceRecognizer(
        [FaceDetection(embedding=(0.98, 0.1, 0.0), confidence=0.95, liveness_score=0.95)]
    )
    matcher2 = LocalFaceMatcher(
        real_rec,
        store,
        FaceConfig(min_liveness_score=0.5, recognition_cooldown_seconds=10.0),
    )
    m1 = matcher2.identify(_frame(), now=20.0)
    assert m1 is not None and m1.profile_id == "user_1"
    assert matcher2.identify(_frame(), now=25.0) is None  # cooled down
    m2 = matcher2.identify(_frame(), now=31.0)
    assert m2 is not None and m2.profile_id == "user_1"
