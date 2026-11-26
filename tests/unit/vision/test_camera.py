"""Unit tests for Phase 6.1 CameraIO, OpenCVCameraIO, and BoundedFrameBuffer."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from jarvis.vision import (
    BoundedFrameBuffer,
    CameraConfig,
    CameraFrame,
    CameraUnavailableError,
    OpenCVCameraIO,
)


class _FakeArray:
    def __init__(self, width: int = 640, height: int = 480) -> None:
        self.shape = (height, width, 3)

    def tobytes(self) -> bytes:
        return b"\x00" * 12


class _FakeCapture:
    def __init__(self, opened: bool = True, ok_read: bool = True) -> None:
        self._opened = opened
        self._ok_read = ok_read
        self.released = False
        self.props: dict[int, float] = {}

    def isOpened(self) -> bool:  # noqa: N802
        return self._opened

    def set(self, prop: int, val: float) -> None:
        self.props[prop] = val

    def get(self, prop: int) -> float:
        return self.props.get(prop, 640.0 if prop == 3 else 480.0)

    def read(self):
        return (self._ok_read, _FakeArray() if self._ok_read else None)

    def release(self) -> None:
        self.released = True


def test_bounded_frame_buffer_caps_size_and_rejects_stale_frames() -> None:
    buf = BoundedFrameBuffer(max_frames=2)
    buf.push(CameraFrame(data=b"1", width=10, height=10, timestamp=1.0, frame_id=1))
    buf.push(CameraFrame(data=b"2", width=10, height=10, timestamp=2.0, frame_id=2))
    buf.push(CameraFrame(data=b"3", width=10, height=10, timestamp=3.0, frame_id=3))

    assert len(buf) == 2
    latest = buf.latest(max_age_seconds=1.0, now=3.5)
    assert latest is not None and latest.frame_id == 3
    assert buf.latest(max_age_seconds=1.0, now=5.0) is None

    buf.clear()
    assert len(buf) == 0


def test_opencv_camera_lazy_import_missing_fallback() -> None:
    io = OpenCVCameraIO()
    assert io.is_open is False  # No initialization on construction
    with patch.dict(sys.modules, {"cv2": None}):
        with pytest.raises(CameraUnavailableError, match="opencv-python"):
            io.list_devices()


def test_opencv_camera_start_fps_cap_and_release(tmp_path) -> None:
    captures: list[_FakeCapture] = []

    def _video_capture(idx: int) -> _FakeCapture:
        cap = _FakeCapture(opened=(idx == 0))
        captures.append(cap)
        return cap

    fake_cv2 = SimpleNamespace(
        VideoCapture=_video_capture,
        CAP_PROP_FRAME_WIDTH=3,
        CAP_PROP_FRAME_HEIGHT=4,
        CAP_PROP_FPS=5,
    )

    io = OpenCVCameraIO(probe_limit=2)
    with patch.dict(sys.modules, {"cv2": fake_cv2}):
        devices = io.list_devices()
        assert len(devices) == 1
        assert devices[0].device_id == "cam-0"
        assert all(c.released for c in captures)

        io.start(CameraConfig(device_id="cam-0", width=640, height=480, max_fps=1.0))
        assert io.is_open is True
        f1 = io.read()
        assert f1 is not None
        # Second immediate read throttled by max_fps=1.0
        assert io.read() is None

        io.close()
        io.close()  # idempotent
        assert io.is_open is False
        assert len(io.buffer) == 0
        assert captures[-1].released is True
        # Zero raw frames written to disk
        assert list(tmp_path.iterdir()) == []
