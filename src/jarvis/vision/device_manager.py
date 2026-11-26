"""Camera device enumeration, selection, and hot-plug tracking (Phase 6.1)."""

from __future__ import annotations

from typing import Any

from jarvis.core.event_bus import EventBus
from jarvis.core.events import (
    CameraConnected,
    CameraDisconnected,
    VisionErrorOccurred,
)
from jarvis.vision.camera import CameraIO
from jarvis.vision.models import CameraDeviceInfo, VisionError


class CameraDeviceManager:
    """Tracks available camera devices, preferred selection, and hot-plug events."""

    def __init__(self, camera_io: CameraIO, bus: EventBus) -> None:
        self._io = camera_io
        self._bus = bus
        self._known: dict[str, CameraDeviceInfo] = {}
        self.preferred_device_id: str | None = None
        self._disconnect_count = 0
        self._last_error: str = ""

    @property
    def devices(self) -> tuple[CameraDeviceInfo, ...]:
        return tuple(self._known.values())

    def select_device(self, device_id: str | None) -> None:
        self.preferred_device_id = device_id

    async def refresh_devices(self) -> tuple[CameraDeviceInfo, ...]:
        """Re-query cameras and publish hot-plug connect/disconnect events."""
        try:
            current_list = self._io.list_devices()
        except VisionError as exc:
            self._last_error = str(exc)
            await self._bus.publish(VisionErrorOccurred(detail=str(exc)))
            return tuple(self._known.values())

        current = {d.device_id: d for d in current_list}
        for did, dev in current.items():
            if did not in self._known:
                await self._bus.publish(
                    CameraConnected(device_id=did, device_name=dev.device_name)
                )
        for did, dev in list(self._known.items()):
            if did not in current:
                self._disconnect_count += 1
                await self._bus.publish(
                    CameraDisconnected(device_id=did, device_name=dev.device_name)
                )
        self._known = current
        if self.preferred_device_id and self.preferred_device_id not in current:
            self.preferred_device_id = None
        self._last_error = ""
        return tuple(self._known.values())

    def health_snapshot(self) -> dict[str, Any]:
        """Return device-level health without touching hardware or frame bytes."""
        return {
            "available_count": len(self._known),
            "preferred_device_id": self.preferred_device_id,
            "disconnect_count": self._disconnect_count,
            "last_error": self._last_error,
        }
