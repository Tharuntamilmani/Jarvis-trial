"""Unit tests for Phase 5.4 Local Piper TTS & Barge-In."""

from __future__ import annotations

import asyncio
import struct
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import patch

import pytest

from jarvis.core.event_bus import EventBus
from jarvis.core.events import (
    AudioErrorOccurred,
    AudioLevelUpdated,
    Event,
    VoiceStateChanged,
)
from jarvis.core.lifecycle import LifecycleManager
from jarvis.core.service_registry import ServiceRegistry
from jarvis.voice import (
    AudioDeviceInfo,
    AudioDeviceManager,
    AudioDeviceUnavailableError,
    AudioFrame,
    EnergyVAD,
    PiperTTSEngine,
    SpeechSynthesizer,
    SpeechTranscriber,
    TTSConfig,
    TTSError,
    TranscriptResult,
    VoiceService,
    VoiceState,
    split_sentences,
)
from tests.unit.test_voice_stt import _FakeSTTEngine


def _frame(rms: float, ts: float = 1.0) -> AudioFrame:
    return AudioFrame(
        pcm_s16=b"\x01\x00" * 480,
        sample_rate=16_000,
        channels=1,
        rms=rms,
        timestamp=ts,
    )


def _pcm_bytes(amplitude: int = 4000, samples: int = 440) -> bytes:
    return struct.pack(f"<{samples}h", *([amplitude] * samples))


class _FakePlaybackAudioIO:
    def __init__(self, *, fail_write: bool = False, write_delay: float = 0.0) -> None:
        self.fail_write = fail_write
        self.write_delay = write_delay
        self.played: list[tuple[bytes, int]] = []

    def list_devices(self) -> list[AudioDeviceInfo]:
        return [
            AudioDeviceInfo(
                device_id="mic-0",
                name="Fake Mic",
                is_input=True,
                is_output=False,
                default_sample_rate=16_000,
                channels=1,
            ),
            AudioDeviceInfo(
                device_id="spk-0",
                name="Fake Speaker",
                is_input=False,
                is_output=True,
                default_sample_rate=22_050,
                channels=1,
            ),
        ]

    def open_input(self, config: object) -> None:
        return None

    def close_input(self) -> None:
        return None

    def read_frame(self) -> AudioFrame | None:
        return None

    def write_output(
        self, pcm_s16: bytes, sample_rate: int, *, device_id: str | None = None
    ) -> None:
        del device_id
        if self.fail_write:
            raise AudioDeviceUnavailableError("Speaker disconnected")
        if self.write_delay > 0:
            time.sleep(self.write_delay)
        self.played.append((pcm_s16, sample_rate))

    def close(self) -> None:
        return None


class _FakeTTSEngine:
    def __init__(self, *, delay: float = 0.0, sample_rate: int = 22_050) -> None:
        self.delay = delay
        self._sample_rate = sample_rate
        self.synthesized_texts: list[str] = []

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        self.synthesized_texts.append(text)
        for sentence in split_sentences(text):
            if self.delay > 0:
                await asyncio.sleep(self.delay)
            yield _pcm_bytes(amplitude=4000, samples=440)
            del sentence


class _FakeWakeWord:
    def detect(self, frame: AudioFrame) -> float | None:
        del frame
        return None

    def reset(self) -> None:
        return None


def test_split_sentences_groups_and_preserves_punctuation() -> None:
    parts = split_sentences("First sentence. Second one! Third question? Trailing")
    assert parts == [
        "First sentence.",
        "Second one!",
        "Third question?",
        "Trailing",
    ]
    assert split_sentences("   ") == []


@pytest.mark.asyncio
async def test_piper_engine_raises_clean_error_when_piper_missing(tmp_path: Path) -> None:
    model_file = tmp_path / "en_US-lessac-medium.onnx"
    model_file.write_bytes(b"fake")
    engine = PiperTTSEngine(TTSConfig(model_path=str(model_file)))
    with patch.dict(sys.modules, {"piper": None}):
        with pytest.raises(TTSError, match="piper-tts"):
            async for _ in engine.synthesize("Hello"):
                pass


@pytest.mark.asyncio
async def test_synthesizer_streams_sentences_and_emits_output_levels() -> None:
    bus = EventBus()
    levels: list[AudioLevelUpdated] = []

    async def on_event(event: Event) -> None:
        if isinstance(event, AudioLevelUpdated):
            levels.append(event)

    bus.subscribe(AudioLevelUpdated, on_event)
    audio_io = _FakePlaybackAudioIO()
    engine = _FakeTTSEngine()
    speaking_states: list[bool] = []

    async def on_speaking(speaking: bool) -> None:
        speaking_states.append(speaking)

    synth = SpeechSynthesizer(
        engine=engine,
        audio_io=audio_io,  # type: ignore[arg-type]
        bus=bus,
        config=TTSConfig(auto_speak=True),
        on_speaking_changed=on_speaking,
    )

    try:
        queued = await synth.speak("Hello there. Systems are online.")
        assert queued is True
        await asyncio.sleep(0.05)

        assert engine.synthesized_texts == ["Hello there. Systems are online."]
        assert len(audio_io.played) == 2
        assert any(ev.direction == "output" and ev.level > 0.0 for ev in levels)
        assert speaking_states[0] is True
        assert speaking_states[-1] is False
    finally:
        await synth.close()


@pytest.mark.asyncio
async def test_synthesizer_emits_audio_error_on_device_failure() -> None:
    bus = EventBus()
    errors: list[AudioErrorOccurred] = []

    async def on_error(event: Event) -> None:
        if isinstance(event, AudioErrorOccurred):
            errors.append(event)

    bus.subscribe(AudioErrorOccurred, on_error)
    audio_io = _FakePlaybackAudioIO(fail_write=True)
    synth = SpeechSynthesizer(
        engine=_FakeTTSEngine(),
        audio_io=audio_io,  # type: ignore[arg-type]
        bus=bus,
    )

    try:
        await synth.speak("Test playback failure.")
        await asyncio.sleep(0.05)

        assert len(errors) == 1
        assert "Speaker disconnected" in errors[0].detail
    finally:
        await synth.close()


@pytest.mark.asyncio
async def test_synthesizer_interrupt_prevents_old_utterance_from_resuming() -> None:
    bus = EventBus()
    audio_io = _FakePlaybackAudioIO(write_delay=0.05)
    engine = _FakeTTSEngine(delay=0.02)
    synth = SpeechSynthesizer(
        engine=engine,
        audio_io=audio_io,  # type: ignore[arg-type]
        bus=bus,
    )

    await synth.speak("Sentence one. Sentence two. Sentence three.")
    await asyncio.sleep(0.03)
    await synth.interrupt(reason="stop")
    await asyncio.sleep(0.15)

    assert len(audio_io.played) < 3
    assert synth.speaking is False
    await synth.close()


def _make_voice_service(
    *,
    write_delay: float = 0.06,
    synth_delay: float = 0.03,
) -> tuple[VoiceService, EventBus, _FakePlaybackAudioIO, LifecycleManager]:
    bus = EventBus()
    lifecycle = LifecycleManager(ServiceRegistry(), bus)
    audio_io = _FakePlaybackAudioIO(write_delay=write_delay)
    engine = _FakeTTSEngine(delay=synth_delay)
    synth = SpeechSynthesizer(
        engine=engine,
        audio_io=audio_io,  # type: ignore[arg-type]
        bus=bus,
    )
    vad = EnergyVAD(start_threshold=0.05, end_threshold=0.02, hangover_frames=1)
    stt = _FakeSTTEngine(
        TranscriptResult(text="what time is it", language="en", confidence=0.95)
    )
    svc = VoiceService(
        audio_io=audio_io,  # type: ignore[arg-type]
        device_manager=AudioDeviceManager(audio_io, bus),  # type: ignore[arg-type]
        vad=vad,
        transcriber=SpeechTranscriber(stt, bus),
        bus=bus,
        wake_word=_FakeWakeWord(),
        lifecycle=lifecycle,
        synthesizer=synth,
        listen_timeout_seconds=5.0,
    )
    return svc, bus, audio_io, lifecycle


@pytest.mark.asyncio
async def test_voice_service_barge_in_stop_push_to_talk_vad_and_kill_switch() -> None:
    # 1. User pressing Stop
    svc, bus, audio_io, _ = _make_voice_service()
    states: list[VoiceState] = []

    async def on_state(event: Event) -> None:
        if isinstance(event, VoiceStateChanged):
            states.append(VoiceState(event.state))

    bus.subscribe(VoiceStateChanged, on_state)
    await svc.set_wake_word_enabled(True)
    assert await svc.speak("One. Two. Three.") is True
    await asyncio.sleep(0.02)
    assert svc.state is VoiceState.SPEAKING
    await svc.interrupt_speech(reason="stop")
    assert svc.state is VoiceState.IDLE
    await asyncio.sleep(0.15)
    assert len(audio_io.played) < 3
    await svc.close()

    # 2. User starting push-to-talk while speaking
    svc2, _, audio_io2, _ = _make_voice_service()
    await svc2.set_wake_word_enabled(True)
    assert await svc2.speak("Alpha. Beta. Gamma.") is True
    await asyncio.sleep(0.02)
    assert svc2.state is VoiceState.SPEAKING
    await svc2.start_push_to_talk()
    assert svc2.state is VoiceState.LISTENING
    await asyncio.sleep(0.15)
    assert len(audio_io2.played) < 3
    await svc2.close()

    # 3. Detected speech while JARVIS is speaking
    svc3, _, audio_io3, _ = _make_voice_service()
    await svc3.set_wake_word_enabled(True)
    assert await svc3.speak("Speaking sentence one. Speaking sentence two. Speaking sentence three.") is True
    await asyncio.sleep(0.02)
    assert svc3.state is VoiceState.SPEAKING
    await svc3.process_frame(_frame(0.30, ts=2.0))
    assert svc3.state is VoiceState.LISTENING
    await asyncio.sleep(0.15)
    assert len(audio_io3.played) < 3
    await svc3.close()

    # 4. Kill switch activation
    svc4, _, audio_io4, lifecycle4 = _make_voice_service()
    await svc4.set_wake_word_enabled(True)
    assert await svc4.speak("Kill switch test one. Kill switch test two. Kill switch test three.") is True
    await asyncio.sleep(0.02)
    assert svc4.state is VoiceState.SPEAKING
    await lifecycle4.trigger_kill_switch("emergency")
    assert svc4.state is VoiceState.DISABLED
    await asyncio.sleep(0.15)
    assert len(audio_io4.played) < 3
    await svc4.close()
