"""Shared VoiceController bridging UI controls to core VoiceService."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any

from PySide6.QtCore import QObject

from jarvis.desktop.viewmodels.app_state import AppState
from jarvis.desktop.viewmodels.chat_controller import ChatController
from jarvis.monitoring.logging import get_logger
from jarvis.voice import VoiceService

log = get_logger(__name__)


class VoiceController(QObject):
    """App-owned controller for microphone, wake-word, PTT, and TTS actions."""

    def __init__(
        self,
        state: AppState,
        voice_service: VoiceService | None = None,
        chat_controller: ChatController | None = None,
    ) -> None:
        super().__init__()
        self._state = state
        self._voice = voice_service
        self._chat = chat_controller
        self._tasks: set[asyncio.Task[Any]] = set()
        self._state.voice_transcript_ready.connect(self._on_transcript_ready)

    @property
    def available(self) -> bool:
        return self._voice is not None

    def attach_voice_service(self, voice_service: VoiceService | None) -> None:
        self._voice = voice_service
        self.refresh_devices()

    def attach_chat_controller(self, chat_controller: ChatController | None) -> None:
        self._chat = chat_controller

    # -- Synchronous UI actions (schedule async work safely) -------------------

    def refresh_devices(self) -> None:
        if self._voice is None:
            return
        self._spawn(self._refresh_devices_async())

    async def _refresh_devices_async(self) -> None:
        if self._voice is None:
            return
        devices = await self._voice.refresh_devices()
        payload = [
            {
                "device_id": d.device_id,
                "device_name": d.name,
                "is_input": d.is_input,
                "is_output": d.is_output,
            }
            for d in devices
        ]
        self._state.apply_voice_devices(payload)

    def select_input_device(self, device_id: str) -> None:
        self._state.set_voice_selected_input_device(device_id)
        if self._voice is None:
            return
        try:
            self._voice.select_input_device(device_id)
        except Exception as exc:  # noqa: BLE001
            self._state.apply_voice_error(str(exc))

    def set_wake_word_enabled(self, enabled: bool) -> None:
        if self._voice is None:
            return
        self._spawn(self._voice.set_wake_word_enabled(enabled))

    def set_muted(self, muted: bool) -> None:
        self._state.set_voice_muted(muted)
        if self._voice is None:
            return
        self._spawn(self._voice.set_muted(muted))

    def start_push_to_talk(self) -> None:
        if self._voice is None:
            return
        self._state.set_voice_ptt(True)
        self._spawn(self._voice.start_push_to_talk())

    def stop_push_to_talk(self) -> None:
        self._state.set_voice_ptt(False)
        if self._voice is None:
            return
        self._spawn(self._voice.stop_push_to_talk())

    def stop_speaking(self) -> None:
        if self._voice is None:
            return
        self._spawn(self._voice.interrupt_speech("stop"))

    # -- Pipeline: VoiceTranscriptReady -> ChatController.submit() -------------

    def _on_transcript_ready(self, text: str, confidence: float) -> None:
        del confidence
        clean = text.strip()
        if not clean or self._chat is None:
            return
        try:
            self._chat.submit(clean)
        except Exception:  # noqa: BLE001
            log.exception("failed to submit voice transcript to chat")

    def _spawn(self, coro: Awaitable[Any]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Outside an active loop (e.g. headless sync widget tests), close coro cleanly.
            close = getattr(coro, "close", None)
            if callable(close):
                close()
            return
        task = loop.create_task(self._run_guarded(coro))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run_guarded(self, coro: Awaitable[Any]) -> None:
        try:
            await coro
        except Exception as exc:  # noqa: BLE001
            log.warning("voice action failed: %s", exc)
            self._state.apply_voice_error(str(exc))
