"""Native PySide6 voice & audio controls panel (Phase 5.5)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from jarvis.desktop.viewmodels.app_state import AppState
from jarvis.desktop.viewmodels.voice_controller import VoiceController


class VoiceControlsPanel(QFrame):
    """Voice status, device selector, level meters, wake-word, PTT, mute & stop."""

    def __init__(
        self,
        state: AppState,
        controller: VoiceController | None = None,
        *,
        compact: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("GlassPanel")
        self._state = state
        self._controller = controller
        self._compact = compact
        self._build_ui()
        self._connect_state()
        self._sync_from_state()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12 if self._compact else 18, 10 if self._compact else 16, 12 if self._compact else 18, 10 if self._compact else 16)
        layout.setSpacing(8 if self._compact else 12)

        header = QHBoxLayout()
        title = QLabel("VOICE & AUDIO" if not self._compact else "VOICE")
        title.setObjectName("SectionTitle")
        header.addWidget(title)
        header.addStretch(1)

        self.voice_state_label = QLabel("DISABLED")
        self.voice_state_label.setObjectName("HudSubtitle")
        header.addWidget(self.voice_state_label)

        self.mic_status_label = QLabel("MIC IDLE")
        self.mic_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.mic_status_label)
        layout.addLayout(header)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #ff4d5e; font-size: 11px;")
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        if not self._compact:
            dev_row = QHBoxLayout()
            dev_label = QLabel("Microphone")
            dev_row.addWidget(dev_label)
            self.device_combo = QComboBox()
            self.device_combo.currentIndexChanged.connect(self._on_device_selected)
            dev_row.addWidget(self.device_combo, 1)
            self.refresh_btn = QPushButton("Refresh")
            self.refresh_btn.clicked.connect(self._on_refresh_devices)
            dev_row.addWidget(self.refresh_btn)
            layout.addLayout(dev_row)
        else:
            self.device_combo = None
            self.refresh_btn = None

        # Input & output meters
        meters_row = QHBoxLayout()
        meters_row.setSpacing(8)
        in_lbl = QLabel("In")
        self.input_meter = QProgressBar()
        self.input_meter.setRange(0, 100)
        self.input_meter.setTextVisible(False)
        self.input_meter.setFixedHeight(8)
        out_lbl = QLabel("Out")
        self.output_meter = QProgressBar()
        self.output_meter.setRange(0, 100)
        self.output_meter.setTextVisible(False)
        self.output_meter.setFixedHeight(8)
        meters_row.addWidget(in_lbl)
        meters_row.addWidget(self.input_meter, 1)
        meters_row.addWidget(out_lbl)
        meters_row.addWidget(self.output_meter, 1)
        layout.addLayout(meters_row)

        # Action controls
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self.ptt_btn = QPushButton("Push to Talk")
        self.ptt_btn.pressed.connect(self._on_ptt_pressed)
        self.ptt_btn.released.connect(self._on_ptt_released)
        btn_row.addWidget(self.ptt_btn)

        self.mute_btn = QPushButton("Mute")
        self.mute_btn.setCheckable(True)
        self.mute_btn.toggled.connect(self._on_mute_toggled)
        btn_row.addWidget(self.mute_btn)

        self.stop_speaking_btn = QPushButton("Stop Speaking")
        self.stop_speaking_btn.clicked.connect(self._on_stop_speaking)
        btn_row.addWidget(self.stop_speaking_btn)

        self.wake_word_btn = QPushButton("Wake Word: Off")
        self.wake_word_btn.setCheckable(True)
        self.wake_word_btn.toggled.connect(self._on_wake_toggled)
        if self._compact:
            self.wake_word_btn.setText("Wake: Off")
        btn_row.addWidget(self.wake_word_btn)
        layout.addLayout(btn_row)

        self.partial_label = QLabel("")
        self.partial_label.setStyleSheet("color: #7f95b2; font-size: 11px;")
        self.partial_label.setWordWrap(True)
        self.partial_label.setVisible(False)
        layout.addWidget(self.partial_label)

    def _connect_state(self) -> None:
        self._state.voice_state_changed.connect(
            lambda *_: self._sync_status_badges()
        )
        self._state.voice_levels_updated.connect(self._on_levels_updated)
        self._state.voice_devices_changed.connect(lambda *_: self._sync_devices())
        self._state.voice_error_changed.connect(lambda *_: self._sync_status_badges())
        self._state.voice_muted_changed.connect(lambda *_: self._sync_status_badges())
        self._state.voice_ptt_changed.connect(lambda *_: self._sync_status_badges())
        self._state.voice_transcript_partial.connect(self._on_partial_transcript)
        self._state.voice_transcript_ready.connect(
            lambda *_: self.partial_label.setVisible(False)
        )

    def _sync_from_state(self) -> None:
        self._sync_devices()
        self._sync_status_badges()
        self._on_levels_updated(
            self._state.voice_input_level, self._state.voice_output_level
        )

    def _sync_devices(self) -> None:
        if self.device_combo is None:
            return
        self.device_combo.blockSignals(True)
        try:
            self.device_combo.clear()
            inputs = [d for d in self._state.voice_devices if d.get("is_input")]
            if not inputs:
                self.device_combo.addItem("No microphone detected", "")
                self.device_combo.setEnabled(False)
                return
            self.device_combo.setEnabled(True)
            selected_idx = 0
            for idx, dev in enumerate(inputs):
                name = str(dev.get("device_name") or dev.get("device_id") or "Mic")
                dev_id = str(dev.get("device_id") or "")
                self.device_combo.addItem(name, dev_id)
                if dev_id == self._state.voice_selected_input_device:
                    selected_idx = idx
            self.device_combo.setCurrentIndex(selected_idx)
        finally:
            self.device_combo.blockSignals(False)

    def _sync_status_badges(self) -> None:
        self.voice_state_label.setText(self._state.voice_state.upper())
        if not self._state.voice_mic_available:
            self.mic_status_label.setText("MIC UNAVAILABLE")
            self.mic_status_label.setStyleSheet(
                "color: #ff4d5e; font-weight: bold; font-size: 11px;"
            )
        elif self._state.voice_muted:
            self.mic_status_label.setText("MIC MUTED")
            self.mic_status_label.setStyleSheet(
                "color: #ffb020; font-weight: bold; font-size: 11px;"
            )
        elif self._state.voice_mic_active:
            self.mic_status_label.setText("MIC ACTIVE")
            self.mic_status_label.setStyleSheet(
                "color: #39d98a; font-weight: bold; font-size: 11px;"
            )
        else:
            self.mic_status_label.setText("MIC IDLE")
            self.mic_status_label.setStyleSheet("color: #7f95b2; font-size: 11px;")

        err = self._state.voice_error_detail
        self.error_label.setText(err)
        self.error_label.setVisible(bool(err))

        self.mute_btn.blockSignals(True)
        self.mute_btn.setChecked(self._state.voice_muted)
        self.mute_btn.setText("Unmute" if self._state.voice_muted else "Mute")
        self.mute_btn.blockSignals(False)

        self.wake_word_btn.blockSignals(True)
        self.wake_word_btn.setChecked(self._state.voice_wake_word_enabled)
        prefix = "Wake" if self._compact else "Wake Word"
        self.wake_word_btn.setText(
            f"{prefix}: {'On' if self._state.voice_wake_word_enabled else 'Off'}"
        )
        self.wake_word_btn.blockSignals(False)

        if self._state.voice_ptt_active:
            self.ptt_btn.setStyleSheet(
                "background-color: #1a8f5a; color: #ffffff; font-weight: bold;"
            )
        else:
            self.ptt_btn.setStyleSheet("")

        self.stop_speaking_btn.setEnabled(self._state.voice_state == "speaking")

    def _on_levels_updated(self, input_level: float, output_level: float) -> None:
        self.input_meter.setValue(int(max(0.0, min(1.0, input_level)) * 100))
        self.output_meter.setValue(int(max(0.0, min(1.0, output_level)) * 100))

    def _on_partial_transcript(self, text: str, confidence: float) -> None:
        del confidence
        clean = text.strip()
        self.partial_label.setText(clean)
        self.partial_label.setVisible(bool(clean))

    # -- UI callbacks ----------------------------------------------------------

    def _on_refresh_devices(self) -> None:
        if self._controller is not None:
            self._controller.refresh_devices()

    def _on_device_selected(self, index: int) -> None:
        if self.device_combo is None or self._controller is None or index < 0:
            return
        dev_id = self.device_combo.itemData(index)
        if dev_id:
            self._controller.select_input_device(str(dev_id))

    def _on_ptt_pressed(self) -> None:
        if self._controller is not None:
            self._controller.start_push_to_talk()

    def _on_ptt_released(self) -> None:
        if self._controller is not None:
            self._controller.stop_push_to_talk()

    def _on_mute_toggled(self, checked: bool) -> None:
        if self._controller is not None:
            self._controller.set_muted(checked)

    def _on_stop_speaking(self) -> None:
        if self._controller is not None:
            self._controller.stop_speaking()

    def _on_wake_toggled(self, checked: bool) -> None:
        if self._controller is not None:
            self._controller.set_wake_word_enabled(checked)
