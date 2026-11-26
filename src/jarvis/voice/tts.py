"""Local Piper TTS streaming synthesis and barge-in playback queue (Phase 5.4)."""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from jarvis.core.event_bus import EventBus
from jarvis.core.events import AudioErrorOccurred, AudioLevelUpdated
from jarvis.voice.audio_io import AudioIO, compute_rms_s16
from jarvis.voice.models import AudioError

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


class TTSError(AudioError):
    """Raised when local TTS synthesis or playback fails."""


@dataclass(frozen=True)
class TTSConfig:
    model_path: str = ""
    speaker_id: int | None = None
    length_scale: float = 1.0
    timeout_seconds: float = 20.0
    auto_speak: bool = False
    sample_rate: int = 22_050


def split_sentences(text: str) -> list[str]:
    """Split response text into non-empty sentences for low-latency TTS."""
    clean = text.strip()
    if not clean:
        return []
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(clean) if p.strip()]
    return parts or [clean]


class TTSEngine(Protocol):
    """Local TTS synthesis contract."""

    @property
    def sample_rate(self) -> int: ...

    def synthesize(self, text: str) -> AsyncIterator[bytes]: ...


class PiperTTSEngine:
    """Lazy ``piper-tts`` local synthesizer."""

    def __init__(self, config: TTSConfig | None = None) -> None:
        self._config = config or TTSConfig()
        self._voice: Any | None = None

    @property
    def sample_rate(self) -> int:
        return self._config.sample_rate

    def _load_voice(self) -> Any:
        if self._voice is not None:
            return self._voice
        try:
            from piper import PiperVoice  # type: ignore[import-not-found]
        except ImportError as exc:
            raise TTSError("piper-tts package is not installed") from exc
        if not self._config.model_path:
            raise TTSError("Piper TTS model_path is not configured")
        self._voice = PiperVoice.load(self._config.model_path)
        return self._voice

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        voice = self._load_voice()
        for sentence in split_sentences(text):
            async with asyncio.timeout(self._config.timeout_seconds):
                chunks = await asyncio.to_thread(
                    lambda s=sentence: list(voice.synthesize_stream_raw(s))
                )
            for chunk in chunks:
                if chunk:
                    yield bytes(chunk)


class SpeechSynthesizer:
    """Queued TTS playback with epoch-guarded immediate barge-in."""

    def __init__(
        self,
        engine: TTSEngine,
        audio_io: AudioIO,
        bus: EventBus,
        config: TTSConfig | None = None,
        *,
        on_speaking_changed: Callable[[bool], Any] | None = None,
    ) -> None:
        self._engine = engine
        self._io = audio_io
        self._bus = bus
        self._config = config or TTSConfig()
        self._on_speaking_changed = on_speaking_changed

        self._queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
        self._epoch = 0
        self._muted = False
        self._speaking = False
        self._worker: asyncio.Task[None] | None = None
        self._current_play: asyncio.Task[None] | None = None

    @property
    def speaking(self) -> bool:
        return self._speaking

    @property
    def muted(self) -> bool:
        return self._muted

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)

    async def speak(self, text: str) -> bool:
        """Enqueue ``text`` for local synthesis and playback."""
        clean = text.strip()
        if not clean or self._muted:
            return False
        self._ensure_worker()
        await self._queue.put((self._epoch, clean))
        return True

    async def interrupt(self, reason: str = "barge_in") -> None:
        """Immediately stop current playback and clear queued utterances."""
        del reason
        self._epoch += 1
        self.clear_queue()
        play = self._current_play
        self._current_play = None
        if play is not None and not play.done():
            play.cancel()
            with contextlib.suppress(BaseException):
                await asyncio.gather(play, return_exceptions=True)
        await self._set_speaking(False)

    def clear_queue(self) -> None:
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break

    async def close(self) -> None:
        await self.interrupt("shutdown")
        worker = self._worker
        self._worker = None
        if worker is not None and not worker.done():
            worker.cancel()
            with contextlib.suppress(BaseException):
                await asyncio.gather(worker, return_exceptions=True)

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._playback_loop())

    async def _playback_loop(self) -> None:
        while True:
            epoch, text = await self._queue.get()
            try:
                if epoch != self._epoch or self._muted:
                    continue
                task = asyncio.create_task(self._play_utterance(epoch, text))
                self._current_play = task
                try:
                    await task
                except asyncio.CancelledError:
                    if epoch != self._epoch:
                        continue
                    raise
                finally:
                    if self._current_play is task:
                        self._current_play = None
            finally:
                self._queue.task_done()

    async def _play_utterance(self, epoch: int, text: str) -> None:
        await self._set_speaking(True)
        try:
            async for chunk in self._engine.synthesize(text):
                if epoch != self._epoch or self._muted:
                    return
                rms = compute_rms_s16(chunk)
                await self._bus.publish(
                    AudioLevelUpdated(level=rms, direction="output")
                )
                await asyncio.to_thread(
                    self._io.write_output,
                    chunk,
                    self._engine.sample_rate,
                )
        except asyncio.CancelledError:
            raise
        except AudioError as exc:
            await self._bus.publish(AudioErrorOccurred(detail=str(exc)))
        finally:
            await self._set_speaking(False)

    async def _set_speaking(self, speaking: bool) -> None:
        if self._speaking == speaking:
            return
        self._speaking = speaking
        if self._on_speaking_changed is not None:
            res = self._on_speaking_changed(speaking)
            if asyncio.iscoroutine(res):
                await res
