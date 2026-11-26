"""Unit tests for Phase 6.5 VisionDiagnostics & VisionRecoveryManager."""

from __future__ import annotations

import json

import pytest

from jarvis.core.event_bus import EventBus
from jarvis.core.events import VisionErrorOccurred
from jarvis.vision import (
    CameraDeviceInfo,
    CameraDeviceManager,
    CameraFrame,
    VisionDiagnostics,
    VisionRecoveryConfig,
    VisionRecoveryManager,
    VisionService,
    VisionState,
)
from tests.unit.vision.test_device_manager import _FakeCameraIO


def test_vision_diagnostics_tracks_latencies_handles_and_rejects_persistence() -> None:
    with pytest.raises(ValueError, match="prohibited"):
        VisionDiagnostics(persist_raw_frames=True)

    diag = VisionDiagnostics()
    diag.record_latency("frame", 8.5)
    diag.record_latency("recognition", 42.0)
    diag.record_latency("gesture", 14.0)
    diag.record_fps(15.0)
    diag.mark_handle_opened("cam-0")
    assert diag.has_handle_leaks() is True
    diag.mark_handle_closed("cam-0")
    assert diag.has_handle_leaks() is False

    snap = diag.snapshot(state=VisionState.IDLE, camera_available=True, disconnect_count=1)
    assert snap["persist_raw_frames"] is False
    assert snap["handle_leak_detected"] is False
    assert snap["capture_fps"] == 15.0
    assert snap["latency_ms"]["frame"]["last"] == 8.5
    assert snap["latency_ms"]["recognition"]["last"] == 42.0
    assert snap["latency_ms"]["gesture"]["last"] == 14.0
    assert snap["disconnect_count"] == 1


@pytest.mark.asyncio
async def test_camera_disconnect_recovery_never_silently_reactivates_capture() -> None:
    bus = EventBus()
    errors: list[VisionErrorOccurred] = []
    bus.subscribe(VisionErrorOccurred, errors.append)

    cam0 = CameraDeviceInfo("cam-0", "Integrated Cam", 0)
    io = _FakeCameraIO([cam0])
    mgr = CameraDeviceManager(io, bus)
    diag = VisionDiagnostics()
    recovery = VisionRecoveryManager(
        bus, diag, VisionRecoveryConfig(capture_stall_seconds=2.0, max_recovery_attempts=2)
    )
    svc = VisionService(
        camera_io=io,
        device_manager=mgr,
        bus=bus,
        diagnostics=diag,
        recovery=recovery,
    )

    await svc.enable()
    assert svc.get_state() is VisionState.ACTIVE
    assert diag.has_handle_leaks() is True

    # Simulate camera disconnect + stall recovery
    io.devices = []
    await svc.recover_from_camera_error("Camera disconnected")
    assert io.is_open is False
    assert diag.has_handle_leaks() is False
    assert svc.get_state() is VisionState.DISABLED
    assert len(errors) >= 1

    # Even when camera hardware returns, recovery only restores availability, NOT capture
    io.devices = [cam0]
    devices = await svc.refresh_devices()
    assert len(devices) == 1
    assert svc.get_state() is VisionState.DISABLED
    assert io.is_open is False

    # Capture stall detection
    assert recovery.is_capture_stalled(last_frame_at=10.0, now=13.5) is True
    assert recovery.is_capture_stalled(last_frame_at=10.0, now=11.0) is False

    # Repeated close() is safe and releases all handles
    await svc.close()
    await svc.close()
    assert diag.has_handle_leaks() is False


@pytest.mark.asyncio
async def test_emergency_shutdown_during_active_capture_releases_handles() -> None:
    bus = EventBus()
    diag = VisionDiagnostics()
    io = _FakeCameraIO([CameraDeviceInfo("cam-0", "Cam", 0)])
    svc = VisionService(
        camera_io=io,
        device_manager=CameraDeviceManager(io, bus),
        bus=bus,
        diagnostics=diag,
    )
    await svc.enable()
    await svc.process_frame(CameraFrame(data=b"\x01\x02", width=1, height=1), now=1.0)
    assert svc.get_state() is VisionState.ACTIVE

    await svc.emergency_shutdown()
    assert svc.get_state() is VisionState.DISABLED
    assert io.is_open is False
    assert diag.has_handle_leaks() is False
    snap = svc.diagnostics_snapshot()
    assert "embedding" not in json.dumps(snap).lower()
    assert "frame_bytes" not in json.dumps(snap).lower()
