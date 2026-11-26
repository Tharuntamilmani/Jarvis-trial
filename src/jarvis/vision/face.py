"""Local face detection, embedding extraction, and matching (Phase 6.2)."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Protocol, runtime_checkable

from jarvis.security.biometrics import BiometricMatch, BiometricStore
from jarvis.vision.models import CameraFrame, VisionError


@dataclass(frozen=True)
class FaceConfig:
    """Configuration for local InsightFace detection & matching."""

    model_name: str = "buffalo_l"
    min_detection_confidence: float = 0.65
    match_threshold: float = 0.62
    min_liveness_score: float = 0.50
    recognition_cooldown_seconds: float = 10.0
    failure_cooldown_seconds: float = 3.0


@dataclass(frozen=True)
class FaceDetection:
    """Single detected face with embedding and anti-spoof confidence."""

    embedding: tuple[float, ...]
    confidence: float
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)
    liveness_score: float = 1.0


@runtime_checkable
class FaceRecognizer(Protocol):
    """Local face detection and embedding extractor contract."""

    def detect_and_embed(self, frame: CameraFrame) -> list[FaceDetection]: ...


class InsightFaceRecognizer:
    """Lazy-loaded InsightFace detector/embedder (local-only)."""

    def __init__(self, config: FaceConfig | None = None) -> None:
        self._config = config or FaceConfig()
        self._app: Any | None = None

    def _load_app(self) -> Any:
        if self._app is not None:
            return self._app
        try:
            from insightface.app import FaceAnalysis  # type: ignore[import-not-found]
        except ImportError as exc:
            raise VisionError(
                "insightface package is not installed"
            ) from exc
        app = FaceAnalysis(name=self._config.model_name)
        app.prepare(ctx_id=-1, det_size=(640, 640))
        self._app = app
        return self._app

    def detect_and_embed(self, frame: CameraFrame) -> list[FaceDetection]:
        try:
            import numpy as np  # type: ignore[import-not-found]
        except ImportError as exc:
            raise VisionError("numpy package is not installed") from exc

        app = self._load_app()
        arr = np.frombuffer(frame.data, dtype=np.uint8).reshape(
            (frame.height, frame.width, frame.channels)
        )
        faces = app.get(arr)
        detections: list[FaceDetection] = []
        for face in faces:
            conf = float(getattr(face, "det_score", 0.0))
            if conf < self._config.min_detection_confidence:
                continue
            liveness = float(getattr(face, "liveness_score", 1.0))
            if liveness < self._config.min_liveness_score:
                continue
            emb = getattr(face, "embedding", None)
            if emb is None:
                continue
            bbox_raw = getattr(face, "bbox", (0, 0, 0, 0))
            bbox = (
                int(bbox_raw[0]),
                int(bbox_raw[1]),
                int(bbox_raw[2]),
                int(bbox_raw[3]),
            )
            detections.append(
                FaceDetection(
                    embedding=tuple(float(x) for x in emb),
                    confidence=conf,
                    bbox=bbox,
                    liveness_score=liveness,
                )
            )
        return detections


class LocalFaceMatcher:
    """Coordinates FaceRecognizer + BiometricStore with cooldown protection."""

    def __init__(
        self,
        recognizer: FaceRecognizer,
        store: BiometricStore,
        config: FaceConfig | None = None,
    ) -> None:
        self._recognizer = recognizer
        self._store = store
        self._config = config or FaceConfig()
        self._last_match_at: dict[str, float] = {}
        self._last_failure_at = 0.0

    def identify(
        self, frame: CameraFrame, *, now: float | None = None
    ) -> BiometricMatch | None:
        """Identify an enrolled user in ``frame`` (returns None if unknown/cooled down)."""
        current = time.monotonic() if now is None else now
        if (
            self._last_failure_at > 0.0
            and (current - self._last_failure_at) < self._config.failure_cooldown_seconds
        ):
            return None

        try:
            detections = self._recognizer.detect_and_embed(frame)
        except VisionError:
            self._last_failure_at = current
            raise

        if not detections:
            return None

        for det in detections:
            if (
                det.confidence < self._config.min_detection_confidence
                or det.liveness_score < self._config.min_liveness_score
            ):
                continue
            match = self._store.match_profile(
                det.embedding, threshold=self._config.match_threshold
            )
            if match is None:
                continue
            last_seen = self._last_match_at.get(match.profile_id, -1e9)
            if (current - last_seen) < self._config.recognition_cooldown_seconds:
                return None
            self._last_match_at[match.profile_id] = current
            return match
        return None
