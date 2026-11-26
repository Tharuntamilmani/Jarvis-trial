"""Unit tests for Phase 5.6 Voice Reliability, Diagnostics & Privacy."""

from __future__ import annotations

import asyncio
import json

import pytest

from jarvis.core.event_bus import EventBus
from jarvis.core.events import (
    AudioDeviceConnected,
    AudioDeviceDisconnected,
    AudioErrorOccurred,
    Event,
)
from jarvis.voice import (
    AudioDeviceInfo,
    AudioDeviceManager,
    AudioFrame,
    EnergyVAD,
    RecoveryConfig,
    SpeechSynthesizer,
    SpeechTranscriber,
    STTError,
    TranscriptResult,
    VoiceDiagnostics,
    VoiceRecoveryManager,
    VoiceService,
    VoiceState,
)
from tests.unit.test_voice_stt import _FakeSTTEngine
from tests.unit.test_voice_tts import (
    _FakePlaybackAudioIO,
    _FakeTTSEngine,
    _FakeWakeWord,
)


def _frame(rms: float, ts: float = 1.0) -> AudioFrame:
    return AudioFrame(
        pcm_s16=b"\x01\x00" * 480,
        sample_rate=16_000,
        channels=1,
        rms=rms,
        timestamp=ts,
    )


def test_diagnostics_latency_stream_leaks_and_privacy() -> None:
    with pytest.raises(ValueError, match="prohibited"):
        VoiceDiagnostics(persist_raw_audio=True)

    diag = VoiceDiagnostics()
    for stage, ms in (
        ("capture", 12.5),
        ("vad", 1.2),
        ("stt", 180.0),
        ("tts", 95.0),
        ("playback", 15.0),
    ):
        diag.record_latency(stage, ms)

    diag.mark_stream_opened("input")
    assert diag.has_stream_leaks() is True
    diag.mark_stream_closed("input")
    assert diag.has_stream_leaks() is False

    snap = diag.snapshot()
    assert snap["persist_raw_audio"] is False
    assert snap["stream_leak_detected"] is False
    assert snap["latency_ms"]["capture"]["last"] == 12.5
    assert snap["latency_ms"]["vad"]["last"] == 1.2
    assert snap["latency_ms"]["stt"]["last"] == 180.0
    assert snap["latency_ms"]["tts"]["last"] == 95.0
    assert snap["latency_ms"]["playback"]["last"] == 15.0
    serialized = json.dumps(snap)
    assert "pcm" not in serialized.lower()


@pytest.mark.asyncio
async def test_device_disconnection_reconnection_and_recovery() -> None:
    bus = EventBus()
    connected: list[AudioDeviceConnected] = []
    disconnected: list[AudioDeviceDisconnected] = []
    errors: list[AudioErrorOccurred] = []

    async def on_event(ev: Event) -> None:
        if isinstance(ev, AudioDeviceConnected):
            connected.append(ev)
        elif isinstance(ev, AudioDeviceDisconnected):
            disconnected.append(ev)
        elif isinstance(ev, AudioErrorOccurred):
            errors.append(ev)

    bus.subscribe(AudioDeviceConnected, on_event)
    bus.subscribe(AudioDeviceDisconnected, on_event)
    bus.subscribe(AudioErrorOccurred, on_event)

    io = _FakePlaybackAudioIO()
    mgr = AudioDeviceManager(io, bus)  # type: ignore[arg-type]
    svc = VoiceService(
        audio_io=io,  # type: ignore[arg-type]
        device_manager=mgr,
        vad=EnergyVAD(),
        transcriber=SpeechTranscriber(
            _FakeSTTEngine(TranscriptResult(text="hello", language="en", confidence=0.9)),
            bus,
        ),
        bus=bus,
        wake_word=_FakeWakeWord(),
    )

    devices = await svc.refresh_devices()
    assert len(devices) == 2
    assert len(connected) == 2

    # Simulate hot-plug disconnect then reconnect via device failure recovery
    io.list_devices = lambda: []  # type: ignore[method-assign]
    await svc.recover_from_device_error("Mic unplugged")
    assert len(errors) == 1
    assert len(disconnected) == 2

    io.list_devices = lambda: [  # type: ignore[method-assign]
        AudioDeviceInfo("mic-2", "Replacement Mic", True, False, 16_000, 1)
    ]
    reconnected = await svc.refresh_devices()
    assert len(reconnected) == 1
    assert reconnected[0].device_id == "mic-2"
    await svc.close()


@pytest.mark.asyncio
async def test_forced_shutdown_during_recording_and_playback_releases_streams() -> None:
    bus = EventBus()
    diag = VoiceDiagnostics()
    io = _FakePlaybackAudioIO(write_delay=0.08)
    synth = SpeechSynthesizer(_FakeTTSEngine(delay=0.02), io, bus)  # type: ignore[arg-type]
    svc = VoiceService(
        audio_io=io,  # type: ignore[arg-type]
        device_manager=AudioDeviceManager(io, bus),  # type: ignore[arg-type]
        vad=EnergyVAD(),
        transcriber=SpeechTranscriber(
            _FakeSTTEngine(TranscriptResult(text="hello", language="en", confidence=0.9)),
            bus,
        ),
        bus=bus,
        wake_word=_FakeWakeWord(),
        synthesizer=synth,
        diagnostics=diag,
    )

    # Forced shutdown during active PTT recording
    diag.mark_stream_opened("input")
    assert await svc.start_push_to_talk() is True
    await svc.process_frame(_frame(0.5, ts=1.0))
    await svc.emergency_shutdown()
    assert svc.state is VoiceState.DISABLED
    assert diag.has_stream_leaks() is False

    # Forced shutdown during active TTS playback
    await svc.set_enabled(True)
    diag.mark_stream_opened("output")
    assert await svc.speak("Sentence one. Sentence two. Sentence three.") is True
    await asyncio.sleep(0.02)
    await svc.emergency_shutdown()
    assert svc.state is VoiceState.DISABLED
    assert diag.has_stream_leaks() is False
    assert synth.speaking is False
    await svc.close()


@pytest.mark.asyncio
async def test_stuck_state_and_stt_timeout_recovery() -> None:
    class _TimeoutSTTEngine:
        async def transcribe(
            self,
            pcm_s16: bytes,
            sample_rate: int,
            *,
            language: str | None = None,
        ) -> TranscriptResult:
            del pcm_s16, sample_rate, language
            raise STTError("STT timed out")

    bus = EventBus()
    diag = VoiceDiagnostics()
    recovery = VoiceRecoveryManager(
        bus,
        diag,
        RecoveryConfig(
            max_listening_seconds=1.0,
            max_processing_seconds=1.0,
            max_speaking_seconds=1.0,
        ),
    )
    io = _FakePlaybackAudioIO()
    svc = VoiceService(
        audio_io=io,  # type: ignore[arg-type]
        device_manager=AudioDeviceManager(io, bus),  # type: ignore[arg-type]
        vad=EnergyVAD(start_threshold=0.05, end_threshold=0.02, hangover_frames=1),
        transcriber=SpeechTranscriber(_TimeoutSTTEngine(), bus),  # type: ignore[arg-type]
        bus=bus,
        wake_word=_FakeWakeWord(),
        diagnostics=diag,
        recovery=recovery,
    )

    # Stuck LISTENING recovery
    await svc.start_push_to_talk()
    assert svc.state is VoiceState.LISTENING
    assert await svc.check_stuck_state(now=svc._state_entered_at + 5.0) is True  # noqa: SLF001
    assert svc.state is VoiceState.IDLE

    # STT timeout recovery: returns None, clears buffer, records STT latency, returns to IDLE
    await svc.start_push_to_talk()
    await svc.process_frame(_frame(0.5, ts=1.0))
    res = await svc.stop_push_to_talk()
    assert res is None
    assert svc.state is VoiceState.IDLE
    snap = svc.diagnostics_snapshot()
    assert snap["latency_ms"]["stt"]["count"] == 1
    assert "STT timed out" not in json.dumps(snap)
    await svc.close()
