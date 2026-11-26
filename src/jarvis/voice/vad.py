"""Dependency-free EnergyVAD with hysteresis and noise-floor adaptation (Phase 5.1)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from jarvis.voice.models import AudioFrame


class VADEventType(StrEnum):
    SPEECH_STARTED = "speech_started"
    SPEECH_ENDED = "speech_ended"


@dataclass(frozen=True)
class VADEvent:
    event_type: VADEventType
    frame: AudioFrame


class VADEngine(Protocol):
    """Voice Activity Detection contract."""

    def process(self, frame: AudioFrame) -> VADEvent | None: ...

    def reset(self) -> None: ...


class EnergyVAD:
    """Lightweight RMS VAD with start/end hysteresis, hangover, and noise floor."""

    def __init__(
        self,
        *,
        start_threshold: float = 0.03,
        end_threshold: float = 0.015,
        hangover_frames: int = 10,
        noise_floor: float = 0.005,
        adaptive_alpha: float = 0.05,
    ) -> None:
        if end_threshold > start_threshold:
            raise ValueError("end_threshold must be <= start_threshold")
        self._start_threshold = float(start_threshold)
        self._end_threshold = float(end_threshold)
        self._hangover_frames = max(1, int(hangover_frames))
        self._noise_floor = float(noise_floor)
        self._adaptive_alpha = float(adaptive_alpha)

        self._speaking = False
        self._silence_count = 0

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    @property
    def noise_floor(self) -> float:
        return self._noise_floor

    def calibrate(self, ambient_frames: list[AudioFrame]) -> float:
        """Calibrate baseline noise floor from ambient silence frames."""
        if not ambient_frames:
            return self._noise_floor
        avg = sum(f.rms for f in ambient_frames) / len(ambient_frames)
        self._noise_floor = max(0.001, avg)
        return self._noise_floor

    def reset(self) -> None:
        self._speaking = False
        self._silence_count = 0

    def process(self, frame: AudioFrame) -> VADEvent | None:
        effective_start = max(self._start_threshold, self._noise_floor * 2.5)
        effective_end = max(self._end_threshold, self._noise_floor * 1.5)

        if not self._speaking:
            if frame.rms >= effective_start:
                self._speaking = True
                self._silence_count = 0
                return VADEvent(VADEventType.SPEECH_STARTED, frame)
            # Adapt noise floor slowly during silence.
            self._noise_floor = (
                (1.0 - self._adaptive_alpha) * self._noise_floor
                + self._adaptive_alpha * frame.rms
            )
            return None

        if frame.rms >= effective_end:
            self._silence_count = 0
            return None

        self._silence_count += 1
        if self._silence_count >= self._hangover_frames:
            self._speaking = False
            self._silence_count = 0
            return VADEvent(VADEventType.SPEECH_ENDED, frame)
        return None
