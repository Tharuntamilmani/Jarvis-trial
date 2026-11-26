"""Floating always-on-top assistant window."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from jarvis.desktop.common.orb import OrbWidget
from jarvis.desktop.viewmodels.app_state import AppState
from jarvis.desktop.viewmodels.vision_controller import VisionController
from jarvis.desktop.viewmodels.voice_controller import VoiceController
from jarvis.desktop.views.vision_controls import VisionControlsPanel
from jarvis.desktop.views.voice_controls import VoiceControlsPanel
from jarvis.desktop.windows_integration import clamp_window_to_screen


class FloatingWindow(QWidget):
    """Compact always-on-top assistant: orb + quick command bar.

    Hide (ESC / auto) preserves state; closing never touches the engine.
    """

    command_submitted = Signal(str)
    expand_workspace_requested = Signal()

    def __init__(
        self,
        state: AppState,
        parent: QWidget | None = None,
        *,
        command_handler: Callable[[str], bool] | None = None,
        voice_controller: VoiceController | None = None,
        vision_controller: VisionController | None = None,
    ) -> None:
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setWindowTitle("JARVIS — Floating")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(300, 480)
        self._state = state
        self._command_handler = command_handler
        self._voice = voice_controller
        self._vision = vision_controller
        self._drag_offset: QPoint | None = None

        self._build_ui()
        self._connect_state()
        self._mirror_current_state()
        self._position_bottom_right()

    # -- UI ---------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        panel = QFrame()
        panel.setObjectName("GlassPanel")
        panel.setStyleSheet(
            "QFrame#GlassPanel { background: rgba(15, 21, 32, 242);"
            " border: 1px solid rgba(148, 163, 184, 58); border-radius: 16px; }"
        )
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header_title = QLabel("JARVIS")
        header_title.setStyleSheet(
            "color: #eef2f7; font-size: 11px; font-weight: 600; letter-spacing: 2px;"
        )
        expand = QLabel("Expand ⤢")
        expand.setStyleSheet("color: #38bdf8; font-size: 11px; font-weight: 500;")
        expand.setCursor(Qt.CursorShape.PointingHandCursor)
        expand.mousePressEvent = lambda _e: self.expand_workspace_requested.emit()
        header.addWidget(header_title)
        header.addStretch(1)
        header.addWidget(expand)
        layout.addLayout(header)

        self.orb = OrbWidget()
        self.orb.setFixedSize(136, 136)
        orb_row = QHBoxLayout()
        orb_row.addStretch(1)
        orb_row.addWidget(self.orb)
        orb_row.addStretch(1)
        layout.addLayout(orb_row)

        self.state_label = QLabel("idle")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_label.setStyleSheet(
            "color: #8b9bb4; font-size: 11px; letter-spacing: 1px;"
        )
        layout.addWidget(self.state_label)

        self.voice_panel = VoiceControlsPanel(
            self._state, self._voice, compact=True
        )
        layout.addWidget(self.voice_panel)
        self.vision_panel = VisionControlsPanel(
            self._state, self._vision, compact=True
        )
        layout.addWidget(self.vision_panel)

        self.command_input = QLineEdit()
        self.command_input.setPlaceholderText("Ask JARVIS… (Enter)")
        self.command_input.returnPressed.connect(self._submit)
        if self._command_handler is None:
            self.command_input.setEnabled(False)
            self.command_input.setPlaceholderText("Quick command unavailable")
        layout.addWidget(self.command_input)

        root.addWidget(panel)

    def _connect_state(self) -> None:
        self._state.assistant_state_changed.connect(self._on_assistant_state)
        self._state.kill_switch_changed.connect(self._on_kill_switch)
        self._state.voice_levels_updated.connect(self._on_voice_levels)
        self._state.voice_state_changed.connect(self._on_voice_state)

    def _mirror_current_state(self) -> None:
        if self._state.kill_switch_active:
            self._on_kill_switch(True)
        else:
            self._on_assistant_state(self._state.assistant_state or "idle")

    def _on_assistant_state(self, state: str) -> None:
        if self._state.kill_switch_active:
            return
        self.orb.set_state(state)
        self.state_label.setText(state)

    def _on_voice_levels(self, input_level: float, output_level: float) -> None:
        self.orb.set_audio_level(max(input_level, output_level))

    def _on_voice_state(
        self, state: str, mic_active: bool, wake_word_enabled: bool
    ) -> None:
        del mic_active, wake_word_enabled
        if self._state.kill_switch_active:
            return
        mapped = {
            "listening": "listening",
            "processing": "thinking",
            "speaking": "speaking",
        }.get(state)
        if mapped:
            self.orb.set_state(mapped)
            self.state_label.setText(state)
        elif self._state.assistant_state:
            self.orb.set_state(self._state.assistant_state)
            self.state_label.setText(self._state.assistant_state)

    def _on_kill_switch(self, active: bool) -> None:
        if active:
            self.orb.set_state("kill_switch")
            self.state_label.setText("emergency stop")
            self.command_input.setEnabled(False)
            self.command_input.setPlaceholderText("Emergency stop engaged")
            return
        self._on_assistant_state(self._state.assistant_state or "idle")
        if self._command_handler is not None:
            self.command_input.setEnabled(True)
            self.command_input.setPlaceholderText("Quick command… (Enter)")

    def _position_bottom_right(self) -> None:
        screen = self.screen() or self.windowHandle().screen()
        if screen is None:
            return
        self.move(clamp_window_to_screen(screen.availableGeometry(), self.size()))

    # -- Interaction ---------------------------------------------------------------

    def _submit(self) -> None:
        text = self.command_input.text().strip()
        if not text or self._command_handler is None:
            return
        if not self._command_handler(text):
            self.state_label.setText("not sent")
            return
        self.command_submitted.emit(text)
        self.command_input.clear()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(
                event.globalPosition().toPoint() - self._drag_offset
            )
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)
