"""Offline wake-word detection (Phase 5.3, zero disk recording)."""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass
from typing import Any, Protocol

from jarvis.voice.models import AudioError, AudioFrame


@dataclass(frozen=True)
class WakeWordConfig:
    phrase: str = "hey_jarvis"
    threshold: float = 0.5
    cooldown_seconds: float = 2.5


class WakeWordEngine(Protocol):
    """Offline wake-word detector contract."""

    def detect(self, frame: AudioFrame) -> float | None: ...

    def reset(self) -> None: ...


class OpenWakeWordEngine:
    """Lazy ``openwakeword`` detector with cooldown & sensitivity threshold."""

    def __init__(self, config: WakeWordConfig | None = None) -> None:
        self._config = config or WakeWordConfig()
        self._model: Any | None = None
        self._last_trigger = float("-inf")

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from openwakeword.model import Model  # type: ignore[import-not-found]
        except ImportError as exc:
            raise AudioError("openwakeword package is not installed") from exc
        self._model = Model(wakeword_models=[self._config.phrase])
        return self._model

    def detect(self, frame: AudioFrame) -> float | None:
        now = frame.timestamp or time.monotonic()
        if (now - self._last_trigger) < self._config.cooldown_seconds:
            return None
        model = self._load_model()
        count = len(frame.pcm_s16) // 2
        if count == 0:
            return None
        import numpy as np  # type: ignore[import-not-found]

        samples = np.asarray(
            struct.unpack(f"<{count}h", frame.pcm_s16[: count * 2]),
            dtype="int16",
        )
        scores = model.predict(samples)
        score = float(scores.get(self._config.phrase, 0.0))
        if score >= self._config.threshold:
            self._last_trigger = now
            return score
        return None

    def reset(self) -> None:
        self._last_trigger = float("-inf")
