"""Camera hardware abstraction, bounded frame buffer, and lazy OpenCV backend."""

from __future__ import annotations

from collections import deque
import time
from typing import Any, Protocol, runtime_checkable

from jarvis.vision.models import (
    CameraConfig,
    CameraDeviceInfo,
    CameraFrame,
    CameraUnavailableError,
)


class BoundedFrameBuffer:
    """In-memory bounded ring buffer for camera frames (never writes to disk)."""

    def __init__(self, max_frames: int = 4) -> None:
        if max_frames <= 0:
            raise ValueError("max_frames must be positive")
        self._max_frames = max_frames
        self._frames: deque[CameraFrame] = deque(maxlen=max_frames)

    @property
    def max_frames(self) -> int:
        return self._max_frames

    def push(self, frame: CameraFrame) -> None:
        self._frames.append(frame)

    def latest(
        self, *, max_age_seconds: float | None = None, now: float | None = None
    ) -> CameraFrame | None:
        if not self._frames:
            return None
        frame = self._frames[-1]
        if max_age_seconds is not None and frame.is_stale(
            now, max_age_seconds=max_age_seconds
        ):
            return None
        return frame

    def clear(self) -> None:
        self._frames.clear()

    def __len__(self) -> int:
        return len(self._frames)


@runtime_checkable
class CameraIO(Protocol):
    """Hardware camera abstraction."""

    @property
    def is_open(self) -> bool: ...

    def list_devices(self) -> list[CameraDeviceInfo]: ...

    def start(self, config: CameraConfig) -> None: ...

    def read(self) -> CameraFrame | None: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...


class OpenCVCameraIO:
    """Lazy-loaded OpenCV camera capture with bounded buffering and FPS cap."""

    def __init__(self, *, probe_limit: int = 4) -> None:
        self._probe_limit = max(1, probe_limit)
        self._cap: Any | None = None
        self._config = CameraConfig()
        self._buffer = BoundedFrameBuffer(self._config.buffer_frames)
        self._last_read_at = 0.0
        self._frame_seq = 0

    @property
    def is_open(self) -> bool:
        return self._cap is not None

    @property
    def buffer(self) -> BoundedFrameBuffer:
        return self._buffer

    @staticmethod
    def _import_cv2() -> Any:
        try:
            import cv2  # type: ignore[import-not-found]
        except ImportError as exc:
            raise CameraUnavailableError(
                "opencv-python package is not installed"
            ) from exc
        return cv2

    def list_devices(self) -> list[CameraDeviceInfo]:
        cv2 = self._import_cv2()
        devices: list[CameraDeviceInfo] = []
        for idx in range(self._probe_limit):
            cap = cv2.VideoCapture(idx)
            try:
                if cap is not None and cap.isOpened():
                    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 640)
                    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 480)
                    devices.append(
                        CameraDeviceInfo(
                            device_id=f"cam-{idx}",
                            device_name=f"Camera {idx}",
                            index=idx,
                            max_width=max(1, w),
                            max_height=max(1, h),
                            is_default=(idx == 0),
                        )
                    )
            finally:
                if cap is not None:
                    cap.release()
        return devices

    def start(self, config: CameraConfig) -> None:
        self.stop()
        cv2 = self._import_cv2()
        index = self._resolve_index(config.device_id)
        cap = cv2.VideoCapture(index)
        if cap is None or not cap.isOpened():
            if cap is not None:
                cap.release()
            raise CameraUnavailableError(
                f"Unable to open camera device {config.device_id or index!r}"
            )
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.height)
        cap.set(cv2.CAP_PROP_FPS, config.max_fps)
        self._cap = cap
        self._config = config
        self._buffer = BoundedFrameBuffer(config.buffer_frames)
        self._last_read_at = 0.0

    def read(self) -> CameraFrame | None:
        if self._cap is None:
            return None
        now = time.monotonic()
        min_interval = 1.0 / self._config.max_fps
        if self._last_read_at > 0.0 and (now - self._last_read_at) < min_interval:
            return None
        ok, frame = self._cap.read()
        if not ok or frame is None:
            raise CameraUnavailableError("Camera frame capture failed")
        self._last_read_at = now
        self._frame_seq += 1
        height, width = frame.shape[:2]
        channels = frame.shape[2] if len(frame.shape) > 2 else 1
        cam_frame = CameraFrame(
            data=frame.tobytes(),
            width=int(width),
            height=int(height),
            channels=int(channels),
            timestamp=now,
            frame_id=self._frame_seq,
        )
        self._buffer.push(cam_frame)
        return cam_frame

    def stop(self) -> None:
        cap = self._cap
        self._cap = None
        self._buffer.clear()
        self._last_read_at = 0.0
        if cap is not None:
            cap.release()

    def close(self) -> None:
        self.stop()

    @staticmethod
    def _resolve_index(device_id: str | None) -> int:
        if not device_id:
            return 0
        if device_id.startswith("cam-") and device_id[4:].isdigit():
            return int(device_id[4:])
        if device_id.isdigit():
            return int(device_id)
        return 0
