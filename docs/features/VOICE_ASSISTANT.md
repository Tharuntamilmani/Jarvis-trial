# Voice Assistant

The voice subsystem (`src/jarvis/voice/`) is **optional** (`pip install -e ".[voice]"`) and processes all audio locally on Windows 11.

---

## Pipeline Components

| Component | Implementation | Role |
| :--- | :--- | :--- |
| **Audio I/O** | `SoundDeviceAudioIO` (`sounddevice`) | Live microphone capture, speaker playback, device selection |
| **VAD** | `EnergyVAD` | Speech-start and speech-end detection |
| **STT** | `FasterWhisperSTT` (`faster-whisper`) | Local speech-to-text transcription |
| **Wake Word** | `OpenWakeWordEngine` (`openwakeword`) | Offline wake-word detection |
| **TTS** | `PiperTTSEngine` (`piper-tts`) & `SpeechSynthesizer` | Local speech synthesis |
| **Recovery & Diagnostics** | `VoiceRecoveryManager`, `VoiceDiagnostics` | Device disconnect handling and health reporting |

---

## Controls & Safety

- **Barge-In:** User speech during active TTS playback interrupts speech output immediately.
- **Modes:** Supports push-to-talk, mute, and wake-word activation via `voice_controls.py` in HUD, Workspace, and Floating views.
- **Shutdown:** Cleanly releases audio streams and worker threads on `ShutdownRequested`.
- **Privacy:** Raw audio buffers are processed locally and are not logged or transmitted to cloud AI providers.
