"""Shared VisionController bridging UI controls to core VisionService (Phase 6.4)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any

from PySide6.QtCore import QObject, Signal

from jarvis.desktop.viewmodels.app_state import AppState
from jarvis.monitoring.logging import get_logger
from jarvis.vision import VisionService

log = get_logger(__name__)

_PERMITTED_GESTURE_ACTIONS = frozenset(
    {"toggle_hud", "toggle_floating", "stop_speaking"}
)


class VisionController(QObject):
    """App-owned controller for opt-in camera, biometrics, and safe gestures."""

    toggle_hud_requested = Signal()
    toggle_floating_requested = Signal()
    stop_speaking_requested = Signal()

    def __init__(
        self,
        state: AppState,
        vision_service: VisionService | None = None,
    ) -> None:
        super().__init__()
        self._state = state
        self._vision = vision_service
        self._tasks: set[asyncio.Task[Any]] = set()
        self._state.vision_gesture_recognized.connect(self._on_gesture_recognized)

    @property
    def available(self) -> bool:
        return self._vision is not None

    def attach_vision_service(self, vision_service: VisionService | None) -> None:
        self._vision = vision_service
        self.refresh_devices()

    def refresh_devices(self) -> None:
        if self._vision is None:
            return
        self._spawn(self._refresh_devices_async())

    async def _refresh_devices_async(self) -> None:
        if self._vision is None:
            return
        devices = await self._vision.refresh_devices()
        payload = [
            {
                "device_id": d.device_id,
                "device_name": d.device_name,
            }
            for d in devices
        ]
        self._state.apply_vision_devices(payload)

    def select_device(self, device_id: str) -> None:
        self._state.set_vision_selected_device(device_id)
        if self._vision is not None:
            self._vision.select_device(device_id)

    def set_camera_enabled(self, enabled: bool) -> None:
        if self._vision is None:
            return
        if enabled:
            self._spawn(self._vision.enable())
        else:
            self._spawn(self._vision.disable())

    def set_face_recognition_enabled(self, enabled: bool) -> None:
        if self._vision is None:
            return
        self._spawn(self._vision.set_face_recognition_enabled(enabled))

    def set_gesture_recognition_enabled(self, enabled: bool) -> None:
        if self._vision is None:
            return
        self._spawn(self._vision.set_gesture_recognition_enabled(enabled))

    def delete_profile(self, profile_id: str) -> bool:
        if self._vision is None:
            return False
        return self._vision.delete_profile(profile_id)

    def _on_gesture_recognized(
        self, gesture: str, action: str, confidence: float
    ) -> None:
        del gesture, confidence
        if self._state.kill_switch_active:
            return
        if action not in _PERMITTED_GESTURE_ACTIONS:
            return
        if action == "toggle_hud":
            self.toggle_hud_requested.emit()
        elif action == "toggle_floating":
            self.toggle_floating_requested.emit()
        elif action == "stop_speaking":
            self.stop_speaking_requested.emit()

    def _spawn(self, coro: Awaitable[Any]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
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
            log.warning("vision action failed: %s", exc)
            self._state.apply_vision_error(str(exc))
