"""Privacy-safe vision diagnostics, latency tracking, and handle leak detection."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from jarvis.vision.models import VisionState

_VISION_STAGES = ("frame", "recognition", "gesture")


@dataclass(frozen=True)
class VisionLatencyStats:
    """Summary statistics (ms) for a single vision processing stage."""

    count: int
    last_ms: float
    avg_ms: float
    max_ms: float


@dataclass
class VisionDiagnostics:
    """Tracks vision latencies and handle lifecycle without frames or embeddings."""

    max_samples: int = 50
    persist_raw_frames: bool = False
    _samples: dict[str, deque[float]] = field(init=False, repr=False)
    _open_handles: set[str] = field(default_factory=set, init=False, repr=False)
    _capture_fps: float = field(default=0.0, init=False)
    _recovery_attempts: int = field(default=0, init=False)
    _last_error_category: str = field(default="", init=False)

    def __post_init__(self) -> None:
        if self.persist_raw_frames:
            raise ValueError("Raw camera frame disk persistence is prohibited by default")
        self._samples = {
            stage: deque(maxlen=max(1, self.max_samples))
            for stage in _VISION_STAGES
        }

    def record_latency(self, stage: str, duration_ms: float) -> None:
        if stage not in self._samples:
            raise KeyError(f"Unknown vision pipeline stage: {stage!r}")
        self._samples[stage].append(max(0.0, float(duration_ms)))

    def record_fps(self, fps: float) -> None:
        self._capture_fps = max(0.0, float(fps))

    def stage_stats(self, stage: str) -> VisionLatencyStats:
        buf = self._samples[stage]
        if not buf:
            return VisionLatencyStats(count=0, last_ms=0.0, avg_ms=0.0, max_ms=0.0)
        return VisionLatencyStats(
            count=len(buf),
            last_ms=buf[-1],
            avg_ms=sum(buf) / len(buf),
            max_ms=max(buf),
        )

    def mark_handle_opened(self, handle_id: str) -> None:
        self._open_handles.add(handle_id)

    def mark_handle_closed(self, handle_id: str) -> None:
        self._open_handles.discard(handle_id)

    def clear_handles(self) -> None:
        self._open_handles.clear()

    def has_handle_leaks(self) -> bool:
        return bool(self._open_handles)

    def record_recovery_attempt(self) -> None:
        self._recovery_attempts += 1

    def record_error(self, category: str) -> None:
        self._last_error_category = category

    def snapshot(
        self,
        *,
        state: VisionState = VisionState.DISABLED,
        camera_available: bool = True,
        disconnect_count: int = 0,
    ) -> dict[str, Any]:
        """Return privacy-safe diagnostics (zero frames, crops, or embeddings)."""
        return {
            "persist_raw_frames": False,
            "state": state.value,
            "camera_available": camera_available,
            "capture_fps": round(self._capture_fps, 2),
            "open_handles": sorted(self._open_handles),
            "handle_leak_detected": self.has_handle_leaks(),
            "disconnect_count": disconnect_count,
            "recovery_attempts": self._recovery_attempts,
            "last_error_category": self._last_error_category,
            "latency_ms": {
                stage: {
                    "count": stats.count,
                    "last": round(stats.last_ms, 2),
                    "avg": round(stats.avg_ms, 2),
                    "max": round(stats.max_ms, 2),
                }
                for stage in _VISION_STAGES
                for stats in (self.stage_stats(stage),)
            },
        }
