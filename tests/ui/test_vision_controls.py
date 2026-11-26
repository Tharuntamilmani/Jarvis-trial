"""UI & EventBridge tests for Phase 6.4 Native Camera Controls & Indicators."""

from __future__ import annotations

import asyncio

import pytest
from PySide6.QtWidgets import QApplication

from jarvis.core.event_bus import EventBus
from jarvis.core.events import (
    AutoLockTriggered,
    CameraConnected,
    CameraDisconnected,
    GestureRecognized,
    PresenceLost,
    UserRecognized,
    VisionErrorOccurred,
    VisionStateChanged,
)
from jarvis.desktop.adapters.event_bridge import EventBridge
from jarvis.desktop.viewmodels.app_state import AppState
from jarvis.desktop.viewmodels.vision_controller import VisionController
from jarvis.desktop.views.vision_controls import VisionControlsPanel
from jarvis.desktop.windows.floating_window import FloatingWindow
from jarvis.desktop.windows.hud_window import HudWindow
from jarvis.desktop.windows.workspace_window import WorkspaceWindow
from jarvis.vision import CameraDeviceInfo, CameraDeviceManager, VisionService
from tests.unit.vision.test_device_manager import _FakeCameraIO


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.mark.asyncio
async def test_event_bridge_forwards_vision_events_to_app_state() -> None:
    _qapp()
    bus = EventBus()
    state = AppState()
    EventBridge(bus, state)

    await bus.publish(CameraConnected(device_id="cam-0", device_name="Integrated Cam"))
    assert len(state.vision_devices) == 1
    assert state.vision_camera_available is True

    await bus.publish(
        VisionStateChanged(
            state="active",
            camera_active=True,
            face_recognition_enabled=True,
            gesture_recognition_enabled=False,
        )
    )
    assert state.vision_state == "active"
    assert state.vision_camera_active is True
    assert state.vision_face_enabled is True

    await bus.publish(
        UserRecognized(profile_id="u1", display_name="Tharun", confidence=0.95)
    )
    assert state.vision_last_user == "Tharun"

    await bus.publish(PresenceLost(last_seen_at=10.0, elapsed_seconds=65.0))
    await bus.publish(AutoLockTriggered(reason="presence_lost"))
    await bus.publish(CameraDisconnected(device_id="cam-0", device_name="Integrated Cam"))
    assert state.vision_camera_available is False

    await bus.publish(VisionErrorOccurred(detail="Camera unplugged"))
    assert state.vision_error_detail == "Camera unplugged"


@pytest.mark.asyncio
async def test_vision_controller_toggles_camera_and_handles_permitted_gestures() -> None:
    _qapp()
    bus = EventBus()
    state = AppState()
    EventBridge(bus, state)

    io = _FakeCameraIO([CameraDeviceInfo("cam-0", "Integrated Cam", 0)])
    svc = VisionService(
        camera_io=io,
        device_manager=CameraDeviceManager(io, bus),
        bus=bus,
    )
    ctrl = VisionController(state, svc)
    hud_toggles: list[bool] = []
    ctrl.toggle_hud_requested.connect(lambda: hud_toggles.append(True))

    ctrl.refresh_devices()
    await asyncio.sleep(0.02)
    assert len(state.vision_devices) == 1

    ctrl.set_camera_enabled(True)
    await asyncio.sleep(0.02)
    assert state.vision_camera_active is True
    assert io.is_open is True

    ctrl.set_face_recognition_enabled(True)
    ctrl.set_gesture_recognition_enabled(True)
    await asyncio.sleep(0.02)
    assert state.vision_face_enabled is True
    assert state.vision_gesture_enabled is True

    await bus.publish(
        GestureRecognized(gesture="open_palm", action="toggle_hud", confidence=0.9)
    )
    assert hud_toggles == [True]

    # Kill switch blocks gesture UI actions
    state.set_kill_switch(True)
    await bus.publish(
        GestureRecognized(gesture="open_palm", action="toggle_hud", confidence=0.9)
    )
    assert hud_toggles == [True]

    ctrl.set_camera_enabled(False)
    await asyncio.sleep(0.02)
    assert state.vision_camera_active is False
    await svc.close()


def test_vision_controls_and_camera_active_indicator_across_all_three_modes() -> None:
    _qapp()
    state = AppState()
    ctrl = VisionController(state)

    panel = VisionControlsPanel(state, ctrl, compact=False)
    hud = HudWindow(state, vision_controller=ctrl)
    floating = FloatingWindow(state, vision_controller=ctrl)
    workspace = WorkspaceWindow(state, vision_controller=ctrl)

    state.apply_vision_devices(
        [{"device_id": "cam-0", "device_name": "Integrated Cam"}]
    )
    assert panel.device_combo is not None
    assert panel.device_combo.count() == 1

    # CAMERA ACTIVE indicator visible across Workspace, HUD, and Floating
    state.apply_vision_state("active", True, True, False)
    assert panel.camera_status_label.text() == "CAMERA ACTIVE"
    assert hud.vision_panel.camera_status_label.text() == "CAMERA ACTIVE"
    assert floating.vision_panel.camera_status_label.text() == "CAMERA ACTIVE"
    assert workspace.vision_controls.camera_status_label.text() == "CAMERA ACTIVE"

    state.apply_vision_error("Camera unavailable")
    assert panel.camera_status_label.text() == "CAMERA UNAVAILABLE"
    assert "Camera unavailable" in panel.error_label.text()
