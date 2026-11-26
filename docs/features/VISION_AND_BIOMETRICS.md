# Vision & Biometrics

The vision subsystem (`src/jarvis/vision/`) is **optional** (`pip install -e ".[vision]"`) and **disabled by default on startup**.

---

## Key Guarantees

- **Explicit Activation Only:** `VisionStateMachine` keeps the camera off on startup. Turning it on displays a visible `CAMERA ACTIVE` indicator in the UI (`vision_controls.py`). Disabling it never triggers automatic reactivation.
- **Instant Kill Switch:** Triggering the kill switch stops camera capture immediately and clears in-memory frames.
- **Zero Raw Biometric Leakage:** Raw camera frames, face crops, and embeddings **never** reach PostgreSQL, log files, or cloud payloads.

---

## Capabilities

| Capability | Implementation | Notes |
| :--- | :--- | :--- |
| **Camera Capture** | `OpenCVCameraIO` (`opencv-python`) | Local frame capture only |
| **Face Recognition** | `InsightFaceRecognizer` (`insightface`) | Opt-in enrollment encrypted with Win32 DPAPI (`BiometricStore`, `.bio.bin` under `data/biometrics/`) |
| **Hand Gestures** | `MediaPipeGestureEngine` & `GestureTracker` (`mediapipe`) | Triggers only permitted UI actions |
| **Diagnostics & Recovery** | `VisionDiagnostics`, `VisionRecoveryManager` | Camera health and error recovery |

See [Privacy](../security/PRIVACY.md) and [Credential Management](../security/CREDENTIAL_MANAGEMENT.md) for DPAPI storage details.
