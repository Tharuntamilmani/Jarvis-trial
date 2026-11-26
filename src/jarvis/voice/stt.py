"""Local Speech-to-Text via faster-whisper with safety bounds (Phase 5.2)."""

from __future__ import annotations

import asyncio
import math
import struct
import time
from dataclasses import dataclass
from typing import Any, Protocol

from jarvis.core.event_bus import EventBus
from jarvis.core.events import VoiceTranscriptPartial, VoiceTranscriptReady
from jarvis.voice.models import AudioError


class STTError(AudioError):
    """Raised when local speech-to-text fails or is unavailable."""


@dataclass(frozen=True)
class STTConfig:
    model_size: str = "base.en"
    language: str | None = None  # None -> auto-detect
    device: str = "cpu"
    compute_type: str = "int8"
    max_utterance_seconds: float = 30.0
    timeout_seconds: float = 15.0
    min_confidence: float = 0.25
    dedup_window_seconds: float = 2.0


@dataclass(frozen=True)
class TranscriptSegment:
    text: str
    start_seconds: float
    end_seconds: float
    confidence: float


@dataclass(frozen=True)
class TranscriptResult:
    text: str
    language: str
    confidence: float
    segments: tuple[TranscriptSegment, ...] = ()
    is_empty: bool = False
    low_confidence: bool = False


class STTEngine(Protocol):
    """Local STT engine contract."""

    async def transcribe(
        self,
        pcm_s16: bytes,
        sample_rate: int,
        *,
        language: str | None = None,
    ) -> TranscriptResult: ...


class FasterWhisperSTT:
    """Lazy ``faster-whisper`` local transcription engine."""

    def __init__(self, config: STTConfig | None = None) -> None:
        self._config = config or STTConfig()
        self._model: Any | None = None

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]
        except ImportError as exc:
            raise STTError("faster-whisper package is not installed") from exc
        self._model = WhisperModel(
            self._config.model_size,
            device=self._config.device,
            compute_type=self._config.compute_type,
        )
        return self._model

    async def transcribe(
        self,
        pcm_s16: bytes,
        sample_rate: int,
        *,
        language: str | None = None,
    ) -> TranscriptResult:
        if not pcm_s16:
            return TranscriptResult(
                text="", language="", confidence=0.0, is_empty=True
            )
        model = self._load_model()
        lang = language if language is not None else self._config.language
        # Convert s16 PCM to float32 normalized [-1, 1] list for Whisper.
        count = len(pcm_s16) // 2
        samples = [
            s / 32768.0 for s in struct.unpack(f"<{count}h", pcm_s16[: count * 2])
        ]

        def _run() -> TranscriptResult:
            import numpy as np  # type: ignore[import-not-found]

            audio = np.asarray(samples, dtype="float32")
            raw_segs, info = model.transcribe(audio, language=lang)
            segs: list[TranscriptSegment] = []
            texts: list[str] = []
            probs: list[float] = []
            for seg in raw_segs:
                txt = str(getattr(seg, "text", "")).strip()
                if not txt:
                    continue
                avg_logprob = float(getattr(seg, "avg_logprob", -0.5))
                conf = min(1.0, max(0.0, math.exp(avg_logprob)))
                segs.append(
                    TranscriptSegment(
                        text=txt,
                        start_seconds=float(getattr(seg, "start", 0.0)),
                        end_seconds=float(getattr(seg, "end", 0.0)),
                        confidence=conf,
                    )
                )
                texts.append(txt)
                probs.append(conf)
            full_text = " ".join(texts).strip()
            mean_conf = sum(probs) / len(probs) if probs else 0.0
            detected = str(getattr(info, "language", lang or ""))
            return TranscriptResult(
                text=full_text,
                language=detected,
                confidence=mean_conf,
                segments=tuple(segs),
                is_empty=not bool(full_text),
                low_confidence=bool(full_text)
                and mean_conf < self._config.min_confidence,
            )

        try:
            async with asyncio.timeout(self._config.timeout_seconds):
                return await asyncio.to_thread(_run)
        except TimeoutError as exc:
            raise STTError("local transcription timed out") from exc


class SpeechTranscriber:
    """Coordinates bounded utterance transcription, dedup, and STT events."""

    def __init__(
        self,
        engine: STTEngine,
        bus: EventBus,
        config: STTConfig | None = None,
    ) -> None:
        self._engine = engine
        self._bus = bus
        self._config = config or STTConfig()
        self._last_text = ""
        self._last_time = float("-inf")

    def bound_pcm(self, pcm_s16: bytes, sample_rate: int) -> bytes:
        """Enforce maximum utterance duration / byte buffer size."""
        max_bytes = int(self._config.max_utterance_seconds * sample_rate * 2)
        return pcm_s16[: max(2, max_bytes)]

    async def emit_partial(self, text: str, confidence: float = 0.0) -> None:
        clean = text.strip()
        if clean:
            await self._bus.publish(
                VoiceTranscriptPartial(text=clean, confidence=confidence)
            )

    async def finalize_utterance(
        self,
        pcm_s16: bytes,
        sample_rate: int,
        *,
        language: str | None = None,
    ) -> TranscriptResult:
        bounded = self.bound_pcm(pcm_s16, sample_rate)
        result = await self._engine.transcribe(
            bounded, sample_rate, language=language
        )
        clean = result.text.strip()
        if not clean:
            return TranscriptResult(
                text="",
                language=result.language,
                confidence=0.0,
                segments=result.segments,
                is_empty=True,
                low_confidence=False,
            )
        if result.confidence < self._config.min_confidence:
            return TranscriptResult(
                text=clean,
                language=result.language,
                confidence=result.confidence,
                segments=result.segments,
                is_empty=False,
                low_confidence=True,
            )

        now = time.monotonic()
        if (
            clean == self._last_text
            and (now - self._last_time) < self._config.dedup_window_seconds
        ):
            return result

        self._last_text = clean
        self._last_time = now
        await self._bus.publish(
            VoiceTranscriptReady(
                text=clean,
                language=result.language,
                confidence=result.confidence,
            )
        )
        return result
