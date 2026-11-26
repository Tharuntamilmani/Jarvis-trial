"""Unit tests for Phase 6.1 CameraDeviceManager."""

from __future__ import annotations

import pytest

from jarvis.core.event_bus import EventBus
from jarvis.core.events import (
    CameraConnected,
    CameraDisconnected,
    VisionErrorOccurred,
)
from jarvis.vision import (
    CameraConfig,
    CameraDeviceInfo,
    CameraDeviceManager,
    CameraFrame,
    CameraUnavailableError,
)


class _FakeCameraIO:
    def __init__(self, devices: list[CameraDeviceInfo]) -> None:
        self.devices = list(devices)
        self.fail = False
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def list_devices(self) -> list[CameraDeviceInfo]:
        if self.fail:
            raise CameraUnavailableError("Camera driver unavailable")
        return list(self.devices)

    def start(self, config: CameraConfig) -> None:
        del config
        self._open = True

    def read(self) -> CameraFrame | None:
        return None

    def stop(self) -> None:
        self._open = False

    def close(self) -> None:
        self._open = False


@pytest.mark.asyncio
async def test_camera_device_manager_hotplug_selection_and_error() -> None:
    bus = EventBus()
    connected: list[CameraConnected] = []
    disconnected: list[CameraDisconnected] = []
    errors: list[VisionErrorOccurred] = []
    bus.subscribe(CameraConnected, connected.append)
    bus.subscribe(CameraDisconnected, disconnected.append)
    bus.subscribe(VisionErrorOccurred, errors.append)

    cam0 = CameraDeviceInfo("cam-0", "Integrated Camera", 0, 1280, 720, True)
    io = _FakeCameraIO([cam0])
    mgr = CameraDeviceManager(io, bus)

    devs = await mgr.refresh_devices()
    assert len(devs) == 1
    assert len(connected) == 1

    mgr.select_device("cam-0")
    assert mgr.preferred_device_id == "cam-0"

    # Disconnect cam-0
    io.devices = []
    devs2 = await mgr.refresh_devices()
    assert devs2 == ()
    assert len(disconnected) == 1
    assert mgr.preferred_device_id is None
    assert mgr.health_snapshot()["disconnect_count"] == 1

    # Driver error emits VisionErrorOccurred cleanly
    io.fail = True
    await mgr.refresh_devices()
    assert len(errors) == 1
