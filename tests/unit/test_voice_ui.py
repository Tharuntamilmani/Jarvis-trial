"""Unit & widget tests for Phase 5.5 Native UI Controls & Audio-Reactive Orb."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication

from jarvis.core.event_bus import EventBus
from jarvis.core.events import (
    AudioDeviceConnected,
    AudioDeviceDisconnected,
    AudioErrorOccurred,
    AudioLevelUpdated,
    VoiceStateChanged,
    VoiceTranscriptPartial,
    VoiceTranscriptReady,
)
from jarvis.desktop.adapters.event_bridge import EventBridge
from jarvis.desktop.common.orb import OrbWidget
from jarvis.desktop.viewmodels.app_state import AppState
from jarvis.desktop.viewmodels.voice_controller import VoiceController
from jarvis.desktop.views.voice_controls import VoiceControlsPanel
from jarvis.desktop.windows.floating_window import FloatingWindow
from jarvis.desktop.windows.hud_window import HudWindow
from jarvis.desktop.windows.workspace_window import WorkspaceWindow
from jarvis.voice import (
    AudioDeviceInfo,
    AudioDeviceManager,
    EnergyVAD,
    SpeechSynthesizer,
    SpeechTranscriber,
    TranscriptResult,
    VoiceService,
)
from tests.unit.test_voice_stt import _FakeSTTEngine
from tests.unit.test_voice_tts import (
    _FakePlaybackAudioIO,
    _FakeTTSEngine,
    _FakeWakeWord,
)


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_orb_widget_smooths_audio_levels_and_clamps() -> None:
    _qapp()
    orb = OrbWidget()
    assert orb.smoothed_level == 0.0

    # A sudden RMS spike is smoothed rather than jumping immediately to 1.0
    orb.set_audio_level(1.0)
    assert 0.20 <= orb.smoothed_level <= 0.30

    orb.set_audio_level(1.5)  # clamped to 1.0
    assert 0.35 <= orb.smoothed_level <= 0.50

    # Decays smoothly when silence returns
    for _ in range(30):
        orb.set_audio_level(0.0)
    assert orb.smoothed_level == 0.0


@pytest.mark.asyncio
async def test_event_bridge_forwards_voice_events_to_app_state() -> None:
    _qapp()
    bus = EventBus()
    state = AppState()
    EventBridge(bus, state)

    await bus.publish(
        AudioDeviceConnected(device_id="mic-1", device_name="USB Mic", direction="input")
    )
    assert len(state.voice_devices) == 1
    assert state.voice_mic_available is True

    await bus.publish(
        VoiceStateChanged(state="listening", mic_active=True, wake_word_enabled=True)
    )
    assert state.voice_state == "listening"
    assert state.voice_mic_active is True
    assert state.voice_wake_word_enabled is True

    await bus.publish(AudioLevelUpdated(level=0.72, direction="input"))
    await bus.publish(AudioLevelUpdated(level=0.44, direction="output"))
    assert state.voice_input_level == pytest.approx(0.72)
    assert state.voice_output_level == pytest.approx(0.44)

    await bus.publish(VoiceTranscriptPartial(text="open cal", confidence=0.6))
    assert state.voice_partial_transcript == "open cal"

    await bus.publish(
        AudioDeviceDisconnected(device_id="mic-1", device_name="USB Mic", direction="input")
    )
    assert state.voice_mic_available is False

    await bus.publish(AudioErrorOccurred(detail="Microphone unplugged"))
    assert state.voice_error_detail == "Microphone unplugged"
    assert state.voice_mic_available is False


@pytest.mark.asyncio
async def test_voice_controller_wires_actions_and_submits_ready_transcript() -> None:
    _qapp()
    bus = EventBus()
    state = AppState()
    EventBridge(bus, state)

    io = _FakePlaybackAudioIO()
    synth = SpeechSynthesizer(_FakeTTSEngine(), io, bus)  # type: ignore[arg-type]
    svc = VoiceService(
        audio_io=io,  # type: ignore[arg-type]
        device_manager=AudioDeviceManager(io, bus),  # type: ignore[arg-type]
        vad=EnergyVAD(),
        transcriber=SpeechTranscriber(
            _FakeSTTEngine(TranscriptResult(text="hello", language="en", confidence=0.9)),
            bus,
        ),
        bus=bus,
        wake_word=_FakeWakeWord(),
        synthesizer=synth,
    )
    chat = MagicMock()
    ctrl = VoiceController(state, svc, chat)

    ctrl.refresh_devices()
    await asyncio.sleep(0.02)
    assert len(state.voice_devices) == 2
    ctrl.select_input_device("mic-0")
    assert state.voice_selected_input_device == "mic-0"

    ctrl.set_wake_word_enabled(True)
    await asyncio.sleep(0.02)
    assert state.voice_wake_word_enabled is True

    ctrl.start_push_to_talk()
    await asyncio.sleep(0.02)
    assert state.voice_ptt_active is True
    assert state.voice_mic_active is True

    ctrl.stop_push_to_talk()
    await asyncio.sleep(0.02)
    assert state.voice_ptt_active is False

    ctrl.set_muted(True)
    await asyncio.sleep(0.02)
    assert state.voice_muted is True

    # Final transcript triggers ChatController.submit; partial never does
    await bus.publish(VoiceTranscriptPartial(text="hel", confidence=0.5))
    chat.submit.assert_not_called()

    await bus.publish(VoiceTranscriptReady(text="hello jarvis", language="en", confidence=0.95))
    chat.submit.assert_called_once_with("hello jarvis")

    await svc.close()


def test_voice_controls_panel_and_three_modes_reflect_voice_state() -> None:
    _qapp()
    state = AppState()
    ctrl = VoiceController(state)

    panel = VoiceControlsPanel(state, ctrl, compact=False)
    hud = HudWindow(state, voice_controller=ctrl)
    floating = FloatingWindow(state, voice_controller=ctrl)
    workspace = WorkspaceWindow(state, voice_controller=ctrl)

    devices = [
        {
            "device_id": AudioDeviceInfo("m1", "Studio Mic", True, False, 16000, 1).device_id,
            "device_name": "Studio Mic",
            "is_input": True,
            "is_output": False,
        }
    ]
    state.apply_voice_devices(devices)
    assert panel.device_combo is not None
    assert panel.device_combo.count() == 1
    assert panel.device_combo.itemText(0) == "Studio Mic"

    state.apply_voice_state("listening", True, True)
    assert panel.voice_state_label.text() == "LISTENING"
    assert panel.mic_status_label.text() == "MIC ACTIVE"
    assert hud.orb.state == "listening"
    assert floating.orb.state == "listening"

    state.apply_voice_level("input", 0.8)
    state.apply_voice_level("output", 0.4)
    assert panel.input_meter.value() == 80
    assert panel.output_meter.value() == 40
    assert hud.orb.smoothed_level > 0.0
    assert floating.orb.smoothed_level > 0.0

    state.apply_voice_state("speaking", False, True)
    assert panel.stop_speaking_btn.isEnabled() is True
    assert hud.orb.state == "speaking"
    assert floating.orb.state == "speaking"

    state.apply_voice_error("Microphone unavailable")
    assert panel.mic_status_label.text() == "MIC UNAVAILABLE"
    assert "Microphone unavailable" in panel.error_label.text()
    assert workspace.settings_view is not None
