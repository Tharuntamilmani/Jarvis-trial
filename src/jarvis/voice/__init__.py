"""Offline voice & audio subsystem (Phase 5). Core-only: never imports PySide6."""

from jarvis.voice.audio_io import (
    AudioIO,
    BoundedAudioBuffer,
    SoundDeviceAudioIO,
    compute_rms_s16,
    resample_pcm_s16,
)
from jarvis.voice.device_manager import AudioDeviceManager
from jarvis.voice.diagnostics import LatencyStats, VoiceDiagnostics
from jarvis.voice.models import (
    AudioConfig,
    AudioDeviceInfo,
    AudioDeviceUnavailableError,
    AudioError,
    AudioFrame,
    VoiceState,
)
from jarvis.voice.recovery import RecoveryConfig, VoiceRecoveryManager
from jarvis.voice.service import VoiceService
from jarvis.voice.stt import (
    FasterWhisperSTT,
    SpeechTranscriber,
    STTConfig,
    STTEngine,
    STTError,
    TranscriptResult,
    TranscriptSegment,
)
from jarvis.voice.tts import (
    PiperTTSEngine,
    SpeechSynthesizer,
    TTSConfig,
    TTSEngine,
    TTSError,
    split_sentences,
)
from jarvis.voice.vad import EnergyVAD, VADEngine, VADEvent, VADEventType
from jarvis.voice.wake_word import (
    OpenWakeWordEngine,
    WakeWordConfig,
    WakeWordEngine,
)

__all__ = [
    "AudioConfig",
    "AudioDeviceInfo",
    "AudioDeviceManager",
    "AudioDeviceUnavailableError",
    "AudioError",
    "AudioFrame",
    "AudioIO",
    "BoundedAudioBuffer",
    "EnergyVAD",
    "FasterWhisperSTT",
    "LatencyStats",
    "OpenWakeWordEngine",
    "PiperTTSEngine",
    "RecoveryConfig",
    "STTConfig",
    "STTEngine",
    "STTError",
    "SoundDeviceAudioIO",
    "SpeechSynthesizer",
    "SpeechTranscriber",
    "TTSConfig",
    "TTSEngine",
    "TTSError",
    "TranscriptResult",
    "TranscriptSegment",
    "VADEngine",
    "VADEvent",
    "VADEventType",
    "VoiceDiagnostics",
    "VoiceRecoveryManager",
    "VoiceService",
    "VoiceState",
    "WakeWordConfig",
    "WakeWordEngine",
    "compute_rms_s16",
    "resample_pcm_s16",
    "split_sentences",
]
