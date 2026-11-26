"""Unit tests for Phase 6.3 VisionService orchestration, greeting, auto-lock & kill switch."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.core.event_bus import EventBus
from jarvis.core.events import (
    AutoLockTriggered,
    GestureRecognized,
    PresenceLost,
    UserRecognized,
)
from jarvis.core.lifecycle import LifecycleManager
from jarvis.core.service_registry import ServiceRegistry
from jarvis.security.biometrics import BiometricStore
from jarvis.vision import (
    CameraConfig,
    CameraDeviceInfo,
    CameraDeviceManager,
    CameraFrame,
    FaceConfig,
    FaceDetection,
    GestureConfig,
    GestureDetection,
    GestureTracker,
    LocalFaceMatcher,
    PresenceConfig,
    VisionService,
    VisionState,
)
from tests.unit.vision.test_device_manager import _FakeCameraIO
from tests.unit.vision.test_face import _FakeFaceRecognizer
from tests.unit.vision.test_gestures import _FakeGestureEngine


def _make_vision_service(
    tmp_path: Path,
    *,
    face_detections: list[FaceDetection] | None = None,
    gesture_detections: list[GestureDetection] | None = None,
    absence_timeout: float = 2.0,
    consecutive_misses: int = 2,
) -> tuple[VisionService, EventBus, _FakeCameraIO, BiometricStore, LifecycleManager]:
    bus = EventBus()
    lifecycle = LifecycleManager(ServiceRegistry(), bus)
    cam0 = CameraDeviceInfo("cam-0", "Integrated Camera", 0, 640, 480, True)
    io = _FakeCameraIO([cam0])
    mgr = CameraDeviceManager(io, bus)
    store = BiometricStore(storage_dir=tmp_path)
    store.enroll_profile("user_1", "Tharun", [1.0, 0.0, 0.0], consent_granted=True)

    face_rec = _FakeFaceRecognizer(face_detections or [])
    matcher = LocalFaceMatcher(
        face_rec, store, FaceConfig(recognition_cooldown_seconds=10.0)
    )
    gest_engine = _FakeGestureEngine(gesture_detections or [])
    tracker = GestureTracker(
        gest_engine, GestureConfig(debounce_frames=1, cooldown_seconds=2.0)
    )
    svc = VisionService(
        camera_io=io,
        device_manager=mgr,
        bus=bus,
        biometrics=store,
        face_matcher=matcher,
        gesture_tracker=tracker,
        lifecycle=lifecycle,
        presence_config=PresenceConfig(
            absence_timeout_seconds=absence_timeout,
            consecutive_misses_required=consecutive_misses,
            auto_lock_enabled=True,
        ),
    )
    return svc, bus, io, store, lifecycle


@pytest.mark.asyncio
async def test_vision_service_greeting_cooldown_and_gestures(tmp_path: Path) -> None:
    face_det = FaceDetection(embedding=(1.0, 0.0, 0.0), confidence=0.95, liveness_score=0.95)
    gest_det = GestureDetection(gesture="two_fingers", confidence=0.92)
    svc, bus, io, _, _ = _make_vision_service(
        tmp_path,
        face_detections=[face_det],
        gesture_detections=[gest_det],
    )
    greetings: list[UserRecognized] = []
    gestures: list[GestureRecognized] = []
    bus.subscribe(UserRecognized, greetings.append)
    bus.subscribe(GestureRecognized, gestures.append)

    assert svc.get_state() is VisionState.DISABLED
    await svc.enable(CameraConfig(device_id="cam-0"))
    assert svc.get_state() is VisionState.ACTIVE
    assert io.is_open is True

    await svc.set_face_recognition_enabled(True)
    await svc.set_gesture_recognition_enabled(True)

    f1 = CameraFrame(data=b"\x00" * 12, width=2, height=2, timestamp=10.0)
    await svc.process_frame(f1, now=10.0)
    assert len(greetings) == 1
    assert greetings[0].profile_id == "user_1"
    assert len(gestures) == 1
    assert gestures[0].action == "toggle_floating"

    # Second immediate frame respects greeting cooldown
    await svc.process_frame(f1, now=12.0)
    assert len(greetings) == 1

    await svc.disable()
    assert svc.get_state() is VisionState.DISABLED
    assert io.is_open is False


@pytest.mark.asyncio
async def test_vision_service_presence_autolock_and_no_auto_unlock(
    tmp_path: Path,
) -> None:
    face_rec = _FakeFaceRecognizer(
        [FaceDetection(embedding=(1.0, 0.0, 0.0), confidence=0.95, liveness_score=0.95)]
    )
    bus = EventBus()
    lifecycle = LifecycleManager(ServiceRegistry(), bus)
    io = _FakeCameraIO([CameraDeviceInfo("cam-0", "Cam", 0)])
    mgr = CameraDeviceManager(io, bus)
    store = BiometricStore(storage_dir=tmp_path)
    store.enroll_profile("user_1", "Tharun", [1.0, 0.0, 0.0], consent_granted=True)

    svc = VisionService(
        camera_io=io,
        device_manager=mgr,
        bus=bus,
        biometrics=store,
        face_matcher=LocalFaceMatcher(face_rec, store),
        lifecycle=lifecycle,
        presence_config=PresenceConfig(
            absence_timeout_seconds=2.0,
            consecutive_misses_required=2,
            auto_lock_enabled=True,
        ),
    )
    lost: list[PresenceLost] = []
    locked: list[AutoLockTriggered] = []
    bus.subscribe(PresenceLost, lost.append)
    bus.subscribe(AutoLockTriggered, locked.append)

    await svc.enable()
    await svc.set_face_recognition_enabled(True)

    # User seen at t=10.0
    await svc.process_frame(CameraFrame(data=b"\x00" * 12, width=2, height=2), now=10.0)

    # User absent: 1 miss is not enough (false absence prevention)
    face_rec.detections = []
    await svc.process_frame(CameraFrame(data=b"\x00" * 12, width=2, height=2), now=13.0)
    assert len(lost) == 0

    # 2nd consecutive miss past absence_timeout triggers PresenceLost + AutoLockTriggered
    await svc.process_frame(CameraFrame(data=b"\x00" * 12, width=2, height=2), now=13.5)
    assert len(lost) == 1
    assert len(locked) == 1
    assert svc.get_state() is VisionState.LOCKED

    # Face reappearing never auto-unlocks LOCKED state!
    face_rec.detections = [
        FaceDetection(embedding=(1.0, 0.0, 0.0), confidence=0.95, liveness_score=0.95)
    ]
    await svc.process_frame(CameraFrame(data=b"\x00" * 12, width=2, height=2), now=14.0)
    assert svc.get_state() is VisionState.LOCKED
    await svc.close()


@pytest.mark.asyncio
async def test_vision_service_kill_switch_halts_and_releases_camera(
    tmp_path: Path,
) -> None:
    svc, _, io, _, lifecycle = _make_vision_service(tmp_path)
    await svc.enable()
    assert svc.get_state() is VisionState.ACTIVE
    assert io.is_open is True

    await lifecycle.trigger_kill_switch("emergency")
    assert svc.get_state() is VisionState.DISABLED
    assert io.is_open is False
    await svc.close()
