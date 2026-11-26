"""Privacy & boundary tests for Phase 6.5 (zero disk, Postgres, log, or cloud leaks)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from jarvis.core.event_bus import EventBus
from jarvis.intelligence.cloud_payload import (
    build_cloud_payload,
    serialize_cloud_body,
)
from jarvis.intelligence.prompts import JARVIS_SYSTEM_PROMPT
from jarvis.intelligence.providers.base import ChatMessage, ChatRequest
from jarvis.security.biometrics import BiometricStore
from jarvis.vision import (
    CameraDeviceInfo,
    CameraDeviceManager,
    CameraFrame,
    FaceDetection,
    LocalFaceMatcher,
    VisionDiagnostics,
    VisionService,
    VisionState,
)
from tests.unit.vision.test_device_manager import _FakeCameraIO
from tests.unit.vision.test_face import _FakeFaceRecognizer


@pytest.mark.asyncio
async def test_vision_never_writes_frames_or_leaks_biometrics_to_logs_or_cloud(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bus = EventBus()
    io = _FakeCameraIO([CameraDeviceInfo("cam-0", "Cam", 0)])
    bio_dir = tmp_path / "bio"
    store = BiometricStore(storage_dir=bio_dir)
    secret_embedding = [0.123456, 0.654321, 0.777888]
    store.enroll_profile("u1", "Tharun", secret_embedding, consent_granted=True)

    diag = VisionDiagnostics()
    svc = VisionService(
        camera_io=io,
        device_manager=CameraDeviceManager(io, bus),
        bus=bus,
        biometrics=store,
        face_matcher=LocalFaceMatcher(
            _FakeFaceRecognizer(
                [FaceDetection(embedding=tuple(secret_embedding), confidence=0.95)]
            ),
            store,
        ),
        diagnostics=diag,
    )

    # Disabled on startup: no capture occurs without explicit enable()
    assert svc.get_state() is VisionState.DISABLED
    assert io.is_open is False

    frame_dir = tmp_path / "frames_check"
    frame_dir.mkdir()
    with caplog.at_level(logging.DEBUG):
        await svc.enable()
        await svc.set_face_recognition_enabled(True)
        await svc.process_frame(
            CameraFrame(data=b"RAW_CAMERA_FRAME_SECRET", width=2, height=2),
            now=5.0,
        )

    # Zero raw camera frames written to disk
    assert list(frame_dir.iterdir()) == []
    # Only the DPAPI-encrypted .bio.bin exists in bio_dir
    assert [p.name for p in bio_dir.iterdir()] == ["u1.bio.bin"]

    # Logs and diagnostics never contain raw frames or embeddings
    snap_json = json.dumps(svc.diagnostics_snapshot())
    assert "RAW_CAMERA_FRAME_SECRET" not in snap_json
    assert "0.123456" not in snap_json
    assert "RAW_CAMERA_FRAME_SECRET" not in caplog.text
    assert "0.123456" not in caplog.text

    # CloudPayload contains only sanitized user message, never vision/biometric data
    req = ChatRequest(
        messages=(ChatMessage(role="user", content="hello jarvis"),),
        model="llama-3.1-8b-instant",
        request_id="req-privacy-1",
    )
    cloud_payload = build_cloud_payload(req, system_prompt=JARVIS_SYSTEM_PROMPT)
    cloud_json = json.dumps(
        serialize_cloud_body(cloud_payload, default_model="llama-3.1-8b-instant")
    )
    assert "RAW_CAMERA_FRAME_SECRET" not in cloud_json
    assert "0.123456" not in cloud_json
    await svc.close()
