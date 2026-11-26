"""Unit tests for Phase 5.3 Offline Wake Word & VoiceService."""

from __future__ import annotations

from jarvis.core.event_bus import EventBus
from jarvis.core.events import VoiceTranscriptReady, WakeWordDetected
from jarvis.core.lifecycle import LifecycleManager
from jarvis.core.service_registry import ServiceRegistry
from jarvis.voice import (
    AudioDeviceManager,
    AudioFrame,
    EnergyVAD,
    SpeechTranscriber,
    TranscriptResult,
    VoiceService,
    VoiceState,
)
from tests.unit.test_voice_audio_vad import _FakeAudioIO
from tests.unit.test_voice_stt import _FakeSTTEngine


class _FakeWakeWord:
    def __init__(self) -> None:
        self.trigger_next = False

    def detect(self, frame: AudioFrame) -> float | None:
        if self.trigger_next:
            self.trigger_next = False
            return 0.91
        return None

    def reset(self) -> None:
        self.trigger_next = False


def _frame(rms: float, ts: float = 1.0) -> AudioFrame:
    return AudioFrame(
        pcm_s16=b"\x01\x00" * 480,
        sample_rate=16_000,
        channels=1,
        rms=rms,
        timestamp=ts,
    )


def _service(lifecycle: LifecycleManager | None = None):
    bus = EventBus()
    io = _FakeAudioIO([])
    wake = _FakeWakeWord()
    stt = _FakeSTTEngine(
        TranscriptResult(text="what time is it", language="en", confidence=0.95)
    )
    svc = VoiceService(
        audio_io=io,  # type: ignore[arg-type]
        device_manager=AudioDeviceManager(io, bus),  # type: ignore[arg-type]
        vad=EnergyVAD(
            start_threshold=0.05, end_threshold=0.02, hangover_frames=1
        ),
        transcriber=SpeechTranscriber(stt, bus),
        bus=bus,
        wake_word=wake,
        lifecycle=lifecycle,
        listen_timeout_seconds=5.0,
    )
    return svc, bus, wake


async def test_wake_word_to_vad_to_transcript_and_buffer_discarded() -> None:
    svc, bus, wake = _service()
    wakes: list[WakeWordDetected] = []
    ready: list[VoiceTranscriptReady] = []
    bus.subscribe(WakeWordDetected, wakes.append)
    bus.subscribe(VoiceTranscriptReady, ready.append)

    assert svc.state is VoiceState.DISABLED
    assert svc.mic_active is False

    await svc.set_wake_word_enabled(True)
    assert svc.state is VoiceState.IDLE

    # Wake word triggers LISTENING.
    wake.trigger_next = True
    await svc.process_frame(_frame(0.01, 10.0))
    assert len(wakes) == 1
    assert svc.state is VoiceState.LISTENING
    assert svc.mic_active is True

    # Speech then silence -> VAD ends utterance -> VoiceTranscriptReady emitted -> IDLE.
    await svc.process_frame(_frame(0.08, 10.1))
    res = await svc.process_frame(_frame(0.01, 10.2))
    assert res is not None and res.text == "what time is it"
    assert len(ready) == 1
    assert svc.state is VoiceState.IDLE
    assert svc.mic_active is False
    assert len(svc._utterance_buffer) == 0  # noqa: SLF001


async def test_push_to_talk_mute_and_kill_switch() -> None:
    bus = EventBus()
    lifecycle = LifecycleManager(ServiceRegistry(), bus)
    svc, bus2, _wake = _service(lifecycle=lifecycle)
    ready: list[VoiceTranscriptReady] = []
    bus2.subscribe(VoiceTranscriptReady, ready.append)

    assert await svc.start_push_to_talk() is True
    assert svc.state is VoiceState.LISTENING
    await svc.process_frame(_frame(0.05, 1.0))
    res = await svc.stop_push_to_talk()
    assert res is not None and res.text == "what time is it"
    assert svc.state is VoiceState.IDLE

    # Muting disables wake word and blocks push-to-talk.
    await svc.set_wake_word_enabled(True)
    await svc.set_muted(True)
    assert svc.wake_word_enabled is False
    assert await svc.start_push_to_talk() is False

    # Kill switch blocks voice activation.
    await svc.set_muted(False)
    await lifecycle.trigger_kill_switch("test")
    assert svc.wake_word_enabled is False
    assert await svc.start_push_to_talk() is False

    await svc.close()
    assert svc.state is VoiceState.DISABLED
