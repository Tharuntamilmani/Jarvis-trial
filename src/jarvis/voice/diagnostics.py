"""Privacy-safe voice diagnostics, latency tracking, and stream leak detection."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

_LATENCY_STAGES = ("capture", "vad", "stt", "tts", "playback")


@dataclass(frozen=True)
class LatencyStats:
    """Summary statistics (ms) for a single pipeline stage."""

    count: int
    last_ms: float
    avg_ms: float
    max_ms: float


@dataclass
class VoiceDiagnostics:
    """Tracks pipeline latency and stream lifecycle without storing audio or text."""

    max_samples: int = 50
    persist_raw_audio: bool = False
    _samples: dict[str, deque[float]] = field(init=False, repr=False)
    _open_streams: set[str] = field(default_factory=set, init=False, repr=False)
    _recovery_count: int = field(default=0, init=False)
    _error_count: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if self.persist_raw_audio:
            raise ValueError("Raw audio disk persistence is prohibited by default")
        self._samples = {
            stage: deque(maxlen=max(1, self.max_samples))
            for stage in _LATENCY_STAGES
        }

    # -- Latency tracking ------------------------------------------------------

    def record_latency(self, stage: str, duration_ms: float) -> None:
        if stage not in self._samples:
            raise KeyError(f"Unknown voice pipeline stage: {stage!r}")
        self._samples[stage].append(max(0.0, float(duration_ms)))

    def stage_stats(self, stage: str) -> LatencyStats:
        buf = self._samples[stage]
        if not buf:
            return LatencyStats(count=0, last_ms=0.0, avg_ms=0.0, max_ms=0.0)
        return LatencyStats(
            count=len(buf),
            last_ms=buf[-1],
            avg_ms=sum(buf) / len(buf),
            max_ms=max(buf),
        )

    # -- Stream leak tracking --------------------------------------------------

    def mark_stream_opened(self, stream_id: str) -> None:
        self._open_streams.add(stream_id)

    def mark_stream_closed(self, stream_id: str) -> None:
        self._open_streams.discard(stream_id)

    @property
    def active_streams(self) -> tuple[str, ...]:
        return tuple(sorted(self._open_streams))

    def has_stream_leaks(self) -> bool:
        return bool(self._open_streams)

    # -- Counters & privacy-safe snapshot -------------------------------------

    def record_recovery(self) -> None:
        self._recovery_count += 1

    def record_error(self) -> None:
        self._error_count += 1

    def snapshot(self) -> dict[str, Any]:
        """Return a privacy-safe diagnostic summary (zero audio bytes or text)."""
        return {
            "persist_raw_audio": False,
            "active_streams": list(self.active_streams),
            "stream_leak_detected": self.has_stream_leaks(),
            "recovery_count": self._recovery_count,
            "error_count": self._error_count,
            "latency_ms": {
                stage: {
                    "count": stats.count,
                    "last": round(stats.last_ms, 2),
                    "avg": round(stats.avg_ms, 2),
                    "max": round(stats.max_ms, 2),
                }
                for stage in _LATENCY_STAGES
                for stats in (self.stage_stats(stage),)
            },
        }
