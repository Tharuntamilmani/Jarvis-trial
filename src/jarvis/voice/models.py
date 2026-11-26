"""Core voice & audio value types (Phase 5.1, zero PySide6)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from jarvis.core.exceptions import JarvisError


class AudioError(JarvisError):
    """Base error for audio I/O and voice processing."""


class AudioDeviceUnavailableError(AudioError):
    """Requested audio hardware or driver is unavailable."""


class VoiceState(StrEnum):
    """High-level state of the voice subsystem."""

    DISABLED = "disabled"
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"


@dataclass(frozen=True)
class AudioDeviceInfo:
    """Metadata for one system audio device."""

    device_id: str
    name: str
    is_input: bool
    is_output: bool
    default_sample_rate: int = 16_000
    channels: int = 1


@dataclass(frozen=True)
class AudioConfig:
    """Normalized audio pipeline configuration."""

    sample_rate: int = 16_000
    channels: int = 1
    frame_ms: int = 30
    ring_buffer_frames: int = 200
    input_device_id: str | None = None
    output_device_id: str | None = None

    @property
    def samples_per_frame(self) -> int:
        return max(1, int(self.sample_rate * self.frame_ms / 1000))


@dataclass(frozen=True)
class AudioFrame:
    """One normalized mono 16-bit signed PCM audio frame."""

    pcm_s16: bytes
    sample_rate: int
    channels: int
    rms: float
    timestamp: float
