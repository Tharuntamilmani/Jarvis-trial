"""Offline vision, local biometrics & gesture subsystem (Phase 6). Core-only: never imports PySide6."""

from jarvis.vision.camera import BoundedFrameBuffer, CameraIO, OpenCVCameraIO
from jarvis.vision.device_manager import CameraDeviceManager
from jarvis.vision.diagnostics import VisionDiagnostics, VisionLatencyStats
from jarvis.vision.face import (
    FaceConfig,
    FaceDetection,
    FaceRecognizer,
    InsightFaceRecognizer,
    LocalFaceMatcher,
)
from jarvis.vision.gestures import (
    DEFAULT_GESTURE_ACTIONS,
    GestureConfig,
    GestureDetection,
    GestureRecognizer,
    GestureTracker,
    MediaPipeGestureEngine,
)
from jarvis.vision.models import (
    CameraConfig,
    CameraDeviceInfo,
    CameraFrame,
    CameraUnavailableError,
    InvalidVisionTransitionError,
    VisionError,
    VisionState,
    VisionStateMachine,
)
from jarvis.vision.recovery import VisionRecoveryConfig, VisionRecoveryManager
from jarvis.vision.service import PresenceConfig, VisionService

__all__ = [
    "BoundedFrameBuffer",
    "CameraConfig",
    "CameraDeviceInfo",
    "CameraDeviceManager",
    "CameraFrame",
    "CameraIO",
    "CameraUnavailableError",
    "DEFAULT_GESTURE_ACTIONS",
    "FaceConfig",
    "FaceDetection",
    "FaceRecognizer",
    "GestureConfig",
    "GestureDetection",
    "GestureRecognizer",
    "GestureTracker",
    "InsightFaceRecognizer",
    "InvalidVisionTransitionError",
    "LocalFaceMatcher",
    "MediaPipeGestureEngine",
    "OpenCVCameraIO",
    "PresenceConfig",
    "VisionDiagnostics",
    "VisionError",
    "VisionLatencyStats",
    "VisionRecoveryConfig",
    "VisionRecoveryManager",
    "VisionService",
    "VisionState",
    "VisionStateMachine",
]
