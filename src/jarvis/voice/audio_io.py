"""Audio I/O, sample-rate normalization, and bounded ring buffering (Phase 5.1)."""

from __future__ import annotations

import math
import struct
import time
from collections import deque
from typing import Any, Protocol

from jarvis.voice.models import (
    AudioConfig,
    AudioDeviceInfo,
    AudioDeviceUnavailableError,
    AudioFrame,
)


def compute_rms_s16(pcm_s16: bytes) -> float:
    """Return normalized RMS amplitude in ``[0.0, 1.0]`` for 16-bit signed PCM."""
    count = len(pcm_s16) // 2
    if count == 0:
        return 0.0
    samples = struct.unpack(f"<{count}h", pcm_s16[: count * 2])
    sq_sum = sum(s * s for s in samples)
    return min(1.0, math.sqrt(sq_sum / count) / 32768.0)


def resample_pcm_s16(
    pcm_s16: bytes, src_rate: int, dst_rate: int
) -> bytes:
    """Linearly resample mono 16-bit signed PCM when sample rates differ."""
    if src_rate <= 0 or dst_rate <= 0:
        raise ValueError("sample rates must be positive")
    if src_rate == dst_rate or len(pcm_s16) < 2:
        return pcm_s16
    count = len(pcm_s16) // 2
    samples = struct.unpack(f"<{count}h", pcm_s16[: count * 2])
    out_count = max(1, int(round(count * dst_rate / src_rate)))
    if count == 1:
        return struct.pack("<h", samples[0]) * out_count
    ratio = (count - 1) / max(1, out_count - 1) if out_count > 1 else 0.0
    out: list[int] = []
    for i in range(out_count):
        pos = i * ratio
        idx = int(pos)
        frac = pos - idx
        s0 = samples[idx]
        s1 = samples[min(idx + 1, count - 1)]
        val = int(round(s0 + (s1 - s0) * frac))
        out.append(max(-32768, min(32767, val)))
    return struct.pack(f"<{out_count}h", *out)


class BoundedAudioBuffer:
    """Fixed-capacity ring buffer preventing unbounded audio memory growth."""

    def __init__(self, max_frames: int = 200) -> None:
        if max_frames < 1:
            raise ValueError("max_frames must be >= 1")
        self._buf: deque[AudioFrame] = deque(maxlen=max_frames)

    def append(self, frame: AudioFrame) -> None:
        self._buf.append(frame)

    def drain(self) -> list[AudioFrame]:
        items = list(self._buf)
        self._buf.clear()
        return items

    def clear(self) -> None:
        self._buf.clear()

    def __len__(self) -> int:
        return len(self._buf)


class AudioIO(Protocol):
    """Abstract audio hardware interface (mockable without mic/speakers)."""

    def list_devices(self) -> list[AudioDeviceInfo]: ...

    def open_input(self, config: AudioConfig) -> None: ...

    def close_input(self) -> None: ...

    def read_frame(self) -> AudioFrame | None: ...

    def write_output(
        self, pcm_s16: bytes, sample_rate: int, *, device_id: str | None = None
    ) -> None: ...

    def close(self) -> None: ...


class SoundDeviceAudioIO:
    """Lazy ``sounddevice`` backend with graceful missing-hardware fallback."""

    def __init__(self) -> None:
        self._sd: Any | None = None
        self._stream: Any | None = None
        self._config: AudioConfig | None = None
        self._device_rate: int = 16_000

    def _import_sd(self) -> Any:
        if self._sd is not None:
            return self._sd
        try:
            import sounddevice as sd  # type: ignore[import-not-found]
        except ImportError as exc:
            raise AudioDeviceUnavailableError(
                "sounddevice package is not installed"
            ) from exc
        self._sd = sd
        return sd

    def list_devices(self) -> list[AudioDeviceInfo]:
        sd = self._import_sd()
        raw = sd.query_devices()
        devices: list[AudioDeviceInfo] = []
        for idx, dev in enumerate(raw):
            devices.append(
                AudioDeviceInfo(
                    device_id=str(idx),
                    name=str(dev.get("name", f"Device {idx}")),
                    is_input=int(dev.get("max_input_channels", 0)) > 0,
                    is_output=int(dev.get("max_output_channels", 0)) > 0,
                    default_sample_rate=int(
                        dev.get("default_samplerate", 16_000)
                    ),
                    channels=max(
                        1,
                        int(
                            dev.get("max_input_channels", 0)
                            or dev.get("max_output_channels", 1)
                        ),
                    ),
                )
            )
        return devices

    def open_input(self, config: AudioConfig) -> None:
        self.close_input()
        sd = self._import_sd()
        dev_idx = (
            int(config.input_device_id)
            if config.input_device_id is not None
            else None
        )
        try:
            self._device_rate = config.sample_rate
            stream = sd.RawInputStream(
                samplerate=config.sample_rate,
                channels=config.channels,
                dtype="int16",
                blocksize=config.samples_per_frame,
                device=dev_idx,
            )
            stream.start()
            self._stream = stream
            self._config = config
        except Exception as exc:
            raise AudioDeviceUnavailableError(
                f"unable to open input stream: {exc}"
            ) from exc

    def close_input(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()

    def read_frame(self) -> AudioFrame | None:
        if self._stream is None or self._config is None:
            return None
        raw, _overflowed = self._stream.read(self._config.samples_per_frame)
        pcm = bytes(raw)
        if self._device_rate != self._config.sample_rate:
            pcm = resample_pcm_s16(
                pcm, self._device_rate, self._config.sample_rate
            )
        return AudioFrame(
            pcm_s16=pcm,
            sample_rate=self._config.sample_rate,
            channels=self._config.channels,
            rms=compute_rms_s16(pcm),
            timestamp=time.monotonic(),
        )

    def write_output(
        self, pcm_s16: bytes, sample_rate: int, *, device_id: str | None = None
    ) -> None:
        sd = self._import_sd()
        dev_idx = int(device_id) if device_id is not None else None
        try:
            with sd.RawOutputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
                device=dev_idx,
            ) as out:
                out.write(pcm_s16)
        except Exception as exc:
            raise AudioDeviceUnavailableError(
                f"unable to write output audio: {exc}"
            ) from exc

    def close(self) -> None:
        self.close_input()
