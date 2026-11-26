"""Unit tests for Phase 5.2 Local Speech-to-Text (stt.py)."""

from __future__ import annotations

import builtins
import pytest

from jarvis.core.event_bus import EventBus
from jarvis.core.events import VoiceTranscriptPartial, VoiceTranscriptReady
from jarvis.voice import (
    FasterWhisperSTT,
    SpeechTranscriber,
    STTConfig,
    STTError,
    TranscriptResult,
)


class _FakeSTTEngine:
    def __init__(self, result: TranscriptResult) -> None:
        self.result = result
        self.seen_bytes = 0

    async def transcribe(self, pcm_s16: bytes, sample_rate: int, *, language=None):
        self.seen_bytes = len(pcm_s16)
        return self.result


def test_faster_whisper_missing_dependency_fails_gracefully(monkeypatch) -> None:
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "faster_whisper":
            raise ImportError("no faster_whisper")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    stt = FasterWhisperSTT()
    with pytest.raises(STTError, match="not installed"):
        stt._load_model()  # noqa: SLF001


async def test_transcriber_emits_partial_and_ready_with_dedup() -> None:
    bus = EventBus()
    partials: list[VoiceTranscriptPartial] = []
    ready: list[VoiceTranscriptReady] = []
    bus.subscribe(VoiceTranscriptPartial, partials.append)
    bus.subscribe(VoiceTranscriptReady, ready.append)

    engine = _FakeSTTEngine(
        TranscriptResult(text="hello jarvis", language="en", confidence=0.92)
    )
    transcriber = SpeechTranscriber(
        engine,
        bus,
        STTConfig(max_utterance_seconds=1.0, dedup_window_seconds=60.0),
    )

    await transcriber.emit_partial("hello", 0.7)
    assert len(partials) == 1
    assert len(ready) == 0

    # Buffer is capped to 1.0s at 16kHz * 2 bytes = 32000 bytes.
    oversized = b"\x01\x00" * 20_000
    res1 = await transcriber.finalize_utterance(oversized, 16_000)
    assert engine.seen_bytes == 32_000
    assert res1.text == "hello jarvis"
    assert len(ready) == 1
    assert ready[0].language == "en"

    # Duplicate transcript within dedup window is suppressed.
    await transcriber.finalize_utterance(b"\x01\x00" * 100, 16_000)
    assert len(ready) == 1


async def test_transcriber_suppresses_empty_and_low_confidence() -> None:
    bus = EventBus()
    ready: list[VoiceTranscriptReady] = []
    bus.subscribe(VoiceTranscriptReady, ready.append)

    empty_eng = _FakeSTTEngine(
        TranscriptResult(text="   ", language="en", confidence=0.9)
    )
    t1 = SpeechTranscriber(empty_eng, bus)
    res_empty = await t1.finalize_utterance(b"\x00\x00" * 100, 16_000)
    assert res_empty.is_empty is True
    assert ready == []

    low_eng = _FakeSTTEngine(
        TranscriptResult(text="mumble", language="en", confidence=0.1)
    )
    t2 = SpeechTranscriber(low_eng, bus, STTConfig(min_confidence=0.3))
    res_low = await t2.finalize_utterance(b"\x00\x00" * 100, 16_000)
    assert res_low.low_confidence is True
    assert ready == []
