"""Unit tests for Phase 5.1 Audio I/O, DeviceManager, and EnergyVAD."""

from __future__ import annotations

import builtins
import struct
import sys

import pytest

from jarvis.core.event_bus import EventBus
from jarvis.core.events import (
    AudioDeviceConnected,
    AudioDeviceDisconnected,
    AudioErrorOccurred,
)
from jarvis.voice import (
    AudioDeviceInfo,
    AudioDeviceManager,
    AudioDeviceUnavailableError,
    AudioFrame,
    BoundedAudioBuffer,
    EnergyVAD,
    SoundDeviceAudioIO,
    VADEventType,
    compute_rms_s16,
    resample_pcm_s16,
)


def _frame(rms: float, ts: float = 0.0) -> AudioFrame:
    return AudioFrame(
        pcm_s16=b"\x00\x00" * 480,
        sample_rate=16_000,
        channels=1,
        rms=rms,
        timestamp=ts,
    )


class _FakeAudioIO:
    def __init__(self, devices: list[AudioDeviceInfo]) -> None:
        self.devices = list(devices)
        self.fail = False

    def list_devices(self) -> list[AudioDeviceInfo]:
        if self.fail:
            raise AudioDeviceUnavailableError("no driver")
        return list(self.devices)

    def open_input(self, config) -> None:
        return None

    def close_input(self) -> None:
        return None

    def read_frame(self):
        return None

    def write_output(self, pcm_s16, sample_rate, *, device_id=None) -> None:
        return None

    def close(self) -> None:
        return None


def test_voice_package_never_imports_pyside6() -> None:
    assert not any(
        name.startswith("jarvis.voice") and "PySide6" in name
        for name in sys.modules
    )


def test_pcm_rms_resampling_and_bounded_buffer() -> None:
    # 480 samples at half-scale (~16384) -> RMS ~ 0.5
    pcm = struct.pack("<480h", *([16384] * 480))
    assert 0.49 <= compute_rms_s16(pcm) <= 0.51

    # 48kHz -> 16kHz downsamples by 3x
    pcm_48k = struct.pack("<48h", *([1000] * 48))
    pcm_16k = resample_pcm_s16(pcm_48k, 48_000, 16_000)
    assert len(pcm_16k) == 16 * 2

    buf = BoundedAudioBuffer(max_frames=2)
    buf.append(_frame(0.1, 1.0))
    buf.append(_frame(0.2, 2.0))
    buf.append(_frame(0.3, 3.0))
    assert len(buf) == 2
    drained = buf.drain()
    assert [f.timestamp for f in drained] == [2.0, 3.0]
    assert len(buf) == 0


def test_sounddevice_missing_dependency_fails_gracefully(monkeypatch) -> None:
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sounddevice":
            raise ImportError("no sounddevice")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    io = SoundDeviceAudioIO()
    with pytest.raises(AudioDeviceUnavailableError, match="not installed"):
        io.list_devices()


async def test_device_manager_hotplug_and_error_events() -> None:
    bus = EventBus()
    events: list[object] = []
    bus.subscribe(AudioDeviceConnected, events.append)
    bus.subscribe(AudioDeviceDisconnected, events.append)
    bus.subscribe(AudioErrorOccurred, events.append)

    mic = AudioDeviceInfo("0", "USB Mic", is_input=True, is_output=False)
    spk = AudioDeviceInfo("1", "Speakers", is_input=False, is_output=True)
    io = _FakeAudioIO([mic])
    mgr = AudioDeviceManager(io, bus)  # type: ignore[arg-type]

    await mgr.refresh_devices()
    assert any(
        isinstance(e, AudioDeviceConnected) and e.device_id == "0"
        for e in events
    )

    io.devices = [spk]
    await mgr.refresh_devices()
    assert any(
        isinstance(e, AudioDeviceDisconnected) and e.device_id == "0"
        for e in events
    )
    assert any(
        isinstance(e, AudioDeviceConnected) and e.device_id == "1"
        for e in events
    )

    io.fail = True
    await mgr.refresh_devices()
    assert any(isinstance(e, AudioErrorOccurred) for e in events)


def test_energy_vad_hysteresis_calibration_and_hangover() -> None:
    vad = EnergyVAD(
        start_threshold=0.05,
        end_threshold=0.02,
        hangover_frames=2,
        noise_floor=0.005,
    )
    vad.calibrate([_frame(0.004), _frame(0.006)])
    assert vad.noise_floor == pytest.approx(0.005)

    # Below start threshold -> silence
    assert vad.process(_frame(0.04)) is None
    assert vad.is_speaking is False

    # Crosses start threshold -> SPEECH_STARTED
    ev1 = vad.process(_frame(0.08))
    assert ev1 is not None and ev1.event_type is VADEventType.SPEECH_STARTED
    assert vad.is_speaking is True

    # Drops between end_threshold and start_threshold -> hysteresis keeps speaking
    assert vad.process(_frame(0.03)) is None
    assert vad.is_speaking is True

    # Below end_threshold: 1st frame in hangover, 2nd frame emits SPEECH_ENDED
    assert vad.process(_frame(0.01)) is None
    ev2 = vad.process(_frame(0.01))
    assert ev2 is not None and ev2.event_type is VADEventType.SPEECH_ENDED
    assert vad.is_speaking is False
