"""VoiceService: coordinates AudioIO, WakeWord, VAD, and local STT (Phase 5.3)."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from jarvis.core.event_bus import EventBus
from jarvis.core.events import (
    AudioErrorOccurred,
    AudioLevelUpdated,
    KillSwitchTriggered,
    VoiceStateChanged,
    WakeWordDetected,
)
from jarvis.voice.audio_io import AudioIO, BoundedAudioBuffer
from jarvis.voice.device_manager import AudioDeviceManager
from jarvis.voice.diagnostics import VoiceDiagnostics
from jarvis.voice.models import AudioConfig, AudioDeviceInfo, AudioFrame, VoiceState
from jarvis.voice.recovery import VoiceRecoveryManager
from jarvis.voice.stt import SpeechTranscriber, STTError, TranscriptResult
from jarvis.voice.tts import SpeechSynthesizer
from jarvis.voice.vad import VADEngine, VADEventType
from jarvis.voice.wake_word import WakeWordConfig, WakeWordEngine

if TYPE_CHECKING:  # pragma: no cover - typing only
    from jarvis.core.lifecycle import LifecycleManager


class VoiceService:
    """Offline voice pipeline controller with strict mic-privacy bounds."""

    def __init__(
        self,
        *,
        audio_io: AudioIO,
        device_manager: AudioDeviceManager,
        vad: VADEngine,
        transcriber: SpeechTranscriber,
        bus: EventBus,
        wake_word: WakeWordEngine | None = None,
        wake_config: WakeWordConfig | None = None,
        synthesizer: SpeechSynthesizer | None = None,
        lifecycle: LifecycleManager | None = None,
        audio_config: AudioConfig | None = None,
        listen_timeout_seconds: float = 8.0,
        diagnostics: VoiceDiagnostics | None = None,
        recovery: VoiceRecoveryManager | None = None,
    ) -> None:
        self._io = audio_io
        self._devices = device_manager
        self._vad = vad
        self._transcriber = transcriber
        self._bus = bus
        self._wake_word = wake_word
        self._wake_config = wake_config or WakeWordConfig()
        self._tts = synthesizer
        self._lifecycle = lifecycle
        self._config = audio_config or AudioConfig()
        self._listen_timeout = float(listen_timeout_seconds)
        self._diagnostics = diagnostics or VoiceDiagnostics()
        self._recovery = recovery or VoiceRecoveryManager(bus, self._diagnostics)

        self._state = VoiceState.DISABLED
        self._state_entered_at = time.monotonic()
        self._muted = False
        self._wake_enabled = False
        self._ptt_active = False
        self._listen_started_at = 0.0
        self._utterance_buffer = BoundedAudioBuffer(
            max_frames=self._config.ring_buffer_frames
        )
        self._smoothed_rms = 0.0
        self._bus.subscribe(KillSwitchTriggered, self._on_kill_switch)

    @property
    def state(self) -> VoiceState:
        return self._state

    @property
    def mic_active(self) -> bool:
        return self._state in (
            VoiceState.LISTENING,
            VoiceState.PROCESSING,
        )

    @property
    def muted(self) -> bool:
        return self._muted

    @property
    def wake_word_enabled(self) -> bool:
        if self._muted or self._kill_switch_active():
            return False
        return self._wake_enabled

    def _kill_switch_active(self) -> bool:
        return (
            self._lifecycle is not None and self._lifecycle.kill_switch_active
        )

    async def refresh_devices(self) -> tuple[AudioDeviceInfo, ...]:
        """Refresh audio device inventory and publish hot-plug events."""
        return await self._devices.refresh_devices()

    def select_input_device(self, device_id: str | None) -> None:
        """Select the preferred input microphone."""
        self._devices.select_input(device_id)

    async def set_enabled(self, enabled: bool) -> None:
        if not enabled:
            self._ptt_active = False
            self._utterance_buffer.clear()
            self._vad.reset()
            self._io.close_input()
            await self._set_state(VoiceState.DISABLED)
            return
        if self._state is VoiceState.DISABLED:
            await self._set_state(VoiceState.IDLE)

    async def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        if self._tts is not None:
            self._tts.set_muted(self._muted)
        if self._muted:
            self._ptt_active = False
            self._utterance_buffer.clear()
            self._vad.reset()
            if self._tts is not None:
                await self._tts.interrupt("muted")
            if self._state in (
                VoiceState.LISTENING,
                VoiceState.PROCESSING,
                VoiceState.SPEAKING,
            ):
                await self._set_state(VoiceState.IDLE)
                return
        await self._publish_state()

    async def speak(self, text: str) -> bool:
        """Speak text locally if TTS is configured and kill switch is off."""
        if self._tts is None or self._muted or self._kill_switch_active():
            return False
        if self._state is VoiceState.DISABLED:
            await self._set_state(VoiceState.IDLE)
        await self._set_state(VoiceState.SPEAKING)
        return await self._tts.speak(text)

    async def interrupt_speech(self, reason: str = "stop") -> None:
        """Stop TTS playback immediately (Stop, PTT, barge-in, kill switch)."""
        if self._tts is not None:
            await self._tts.interrupt(reason)
        if self._state is VoiceState.SPEAKING:
            await self._set_state(VoiceState.IDLE)

    async def set_wake_word_enabled(self, enabled: bool) -> None:
        self._wake_enabled = bool(enabled)
        if self._wake_enabled and self._state is VoiceState.DISABLED:
            await self._set_state(VoiceState.IDLE)
            return
        await self._publish_state()

    async def start_push_to_talk(self) -> bool:
        """Begin push-to-talk capture (takes priority over wake word & TTS)."""
        if self._muted or self._kill_switch_active():
            return False
        await self.interrupt_speech("push_to_talk")
        if self._state is VoiceState.DISABLED:
            await self._set_state(VoiceState.IDLE)
        self._ptt_active = True
        self._utterance_buffer.clear()
        self._vad.reset()
        self._listen_started_at = time.monotonic()
        await self._set_state(VoiceState.LISTENING)
        return True

    async def stop_push_to_talk(self) -> TranscriptResult | None:
        """Finish push-to-talk capture and transcribe the buffered utterance."""
        if not self._ptt_active:
            return None
        self._ptt_active = False
        return await self._finalize_buffered_utterance()

    async def process_frame(self, frame: AudioFrame) -> TranscriptResult | None:
        """Process one normalized audio frame through wake-word / VAD / STT."""
        if self._state is VoiceState.DISABLED or self._muted:
            return None
        if self._kill_switch_active():
            self._ptt_active = False
            self._utterance_buffer.clear()
            self._vad.reset()
            await self.interrupt_speech("kill_switch")
            await self._set_state(VoiceState.IDLE)
            return None

        # Smoothed audio level for orb reactivity.
        self._smoothed_rms = 0.7 * self._smoothed_rms + 0.3 * frame.rms
        await self._bus.publish(
            AudioLevelUpdated(level=self._smoothed_rms, direction="input")
        )

        # Push-to-talk has priority over continuous wake-word listening.
        if self._ptt_active:
            self._utterance_buffer.append(frame)
            return None

        # Barge-in: speech detected while JARVIS is speaking interrupts TTS.
        if self._state is VoiceState.SPEAKING:
            vad_ev = self._vad.process(frame)
            if vad_ev is not None and vad_ev.event_type is VADEventType.SPEECH_STARTED:
                await self.interrupt_speech("barge_in")
                self._utterance_buffer.clear()
                self._utterance_buffer.append(frame)
                self._listen_started_at = frame.timestamp or time.monotonic()
                await self._set_state(VoiceState.LISTENING)
            return None

        if self._state is VoiceState.IDLE:
            if (
                self.wake_word_enabled
                and self._wake_word is not None
            ):
                score = self._wake_word.detect(frame)
                if score is not None:
                    self._utterance_buffer.clear()
                    self._vad.reset()
                    self._listen_started_at = frame.timestamp or time.monotonic()
                    await self._bus.publish(
                        WakeWordDetected(
                            phrase=self._wake_config.phrase, score=score
                        )
                    )
                    await self._set_state(VoiceState.LISTENING)
            return None

        if self._state is VoiceState.LISTENING:
            self._utterance_buffer.append(frame)
            vad_ev = self._vad.process(frame)
            if vad_ev is not None and vad_ev.event_type is VADEventType.SPEECH_ENDED:
                return await self._finalize_buffered_utterance()

            now = frame.timestamp or time.monotonic()
            if (now - self._listen_started_at) >= self._listen_timeout:
                # Listening timed out without completed speech: discard promptly.
                self._utterance_buffer.clear()
                self._vad.reset()
                await self._set_state(VoiceState.IDLE)
            return None

        return None

    async def _finalize_buffered_utterance(self) -> TranscriptResult | None:
        frames = self._utterance_buffer.drain()
        self._vad.reset()
        if not frames:
            await self._set_state(VoiceState.IDLE)
            return None
        await self._set_state(VoiceState.PROCESSING)
        pcm = b"".join(f.pcm_s16 for f in frames)
        t0 = time.perf_counter()
        try:
            return await self._transcriber.finalize_utterance(
                pcm, self._config.sample_rate
            )
        except (STTError, TimeoutError) as exc:
            self._diagnostics.record_error()
            self._diagnostics.record_recovery()
            await self._bus.publish(AudioErrorOccurred(detail=str(exc)))
            return None
        finally:
            self._diagnostics.record_latency(
                "stt", (time.perf_counter() - t0) * 1000.0
            )
            self._utterance_buffer.clear()
            if self._state is not VoiceState.DISABLED:
                await self._set_state(VoiceState.IDLE)

    async def _set_state(self, state: VoiceState) -> None:
        self._state = state
        self._state_entered_at = time.monotonic()
        await self._publish_state()

    def diagnostics_snapshot(self) -> dict[str, Any]:
        """Return privacy-safe voice diagnostics (no raw audio or transcripts)."""
        return self._diagnostics.snapshot()

    async def check_stuck_state(self, *, now: float | None = None) -> bool:
        """Detect and recover from stuck listening/processing/speaking states."""
        if not self._recovery.is_state_stuck(
            self._state, self._state_entered_at, now=now
        ):
            return False
        stuck = self._state
        self._ptt_active = False
        self._utterance_buffer.clear()
        self._vad.reset()
        if self._tts is not None:
            await self._tts.interrupt("stuck_state")
        await self._set_state(VoiceState.IDLE)
        await self._recovery.report_stuck_state(stuck)
        return True

    async def recover_from_device_error(
        self, detail: str
    ) -> tuple[AudioDeviceInfo, ...]:
        """Close broken stream cleanly, emit diagnostic event, and re-probe."""
        self._io.close_input()
        self._diagnostics.mark_stream_closed("input")
        return await self._recovery.recover_device_failure(self._devices, detail)

    async def emergency_shutdown(self) -> None:
        """Immediately halt capture, STT, TTS playback, and release all streams."""
        self._ptt_active = False
        self._utterance_buffer.clear()
        self._vad.reset()
        if self._tts is not None:
            await self._tts.interrupt("emergency_shutdown")
        self._io.close_input()
        self._diagnostics.mark_stream_closed("input")
        self._diagnostics.mark_stream_closed("output")
        await self._set_state(VoiceState.DISABLED)
        self._io.close()

    async def _publish_state(self) -> None:
        await self._bus.publish(
            VoiceStateChanged(
                state=self._state.value,
                mic_active=self.mic_active,
                wake_word_enabled=self.wake_word_enabled,
            )
        )

    async def _on_kill_switch(self, event: KillSwitchTriggered) -> None:
        del event
        if self._tts is not None:
            await self._tts.interrupt("kill_switch")
        await self.set_enabled(False)

    async def close(self) -> None:
        if self._tts is not None:
            await self._tts.close()
        await self.set_enabled(False)
        self._io.close()
        await asyncio.sleep(0)
