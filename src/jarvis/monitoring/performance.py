"""Startup & token-latency profiler and lazy-import verification (Phase 8.4)."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
import time
from typing import Any

LAZY_OPTIONAL_PACKAGES = (
    "cv2",
    "insightface",
    "mediapipe",
    "faster_whisper",
    "piper",
    "openwakeword",
    "torch",
)


def check_lazy_optional_imports(modules: Mapping[str, Any]) -> list[str]:
    """Return any heavy optional packages that were eagerly imported."""
    loaded: list[str] = []
    for pkg in LAZY_OPTIONAL_PACKAGES:
        if pkg in modules and modules[pkg] is not None:
            loaded.append(pkg)
    return loaded


class StartupProfiler:
    """Measures cold startup, subsystem initialization, and first-token latency."""

    def __init__(
        self,
        *,
        cold_start_target_ms: float = 3000.0,
        first_token_target_ms: float = 500.0,
    ) -> None:
        self._cold_target = float(cold_start_target_ms)
        self._token_target = float(first_token_target_ms)
        self._timings: dict[str, float] = {}

    def record(self, phase: str, duration_ms: float) -> None:
        self._timings[phase] = round(max(0.0, float(duration_ms)), 2)

    @contextmanager
    def measure(self, phase: str) -> Iterator[None]:
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.record(phase, (time.perf_counter() - t0) * 1000.0)

    def within_stretch_targets(self) -> bool:
        cold = self._timings.get("cold_start_ms")
        tok = self._timings.get("first_token_ms")
        if cold is not None and cold > self._cold_target:
            return False
        if tok is not None and tok > self._token_target:
            return False
        return True

    def snapshot(self) -> dict[str, float]:
        return dict(self._timings)
