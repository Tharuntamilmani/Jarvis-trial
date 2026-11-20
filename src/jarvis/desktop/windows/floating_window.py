"""Floating always-on-top assistant window."""

from __future__ import annotations

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


class FloatingWindow(QWidget):
    """Compact always-on-top assistant: orb + quick command bar.

    Hide (ESC / auto) preserves state; closing never touches the engine.
    """

    command_submitted = Signal(str)
    expand_workspace_requested = Signal()

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setWindowTitle("JARVIS — Floating")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(280, 340)
        self._state = state
        self._drag_offset: QPoint | None = None

        self._build_ui()
        self._connect_state()
        self._position_bottom_right()

    # -- UI ---------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        panel = QFrame()
        panel.setObjectName("GlassPanel")
        panel.setStyleSheet(
            "QFrame#GlassPanel { background: rgba(13, 23, 41, 230);"
            " border: 1px solid rgba(0, 212, 255, 60); border-radius: 14px; }"
        )
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.orb = OrbWidget()
        self.orb.setFixedSize(130, 130)
        orb_row = QHBoxLayout()
        orb_row.addStretch(1)
        orb_row.addWidget(self.orb)
        orb_row.addStretch(1)
        layout.addLayout(orb_row)

        self.state_label = QLabel("idle")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_label.setStyleSheet("color: #7f95b2; font-size: 11px;")
        layout.addWidget(self.state_label)

        self.command_input = QLineEdit()
        self.command_input.setPlaceholderText("Quick command… (Enter)")
        self.command_input.returnPressed.connect(self._submit)
        layout.addWidget(self.command_input)

        expand = QLabel("⤢ expand")
        expand.setStyleSheet("color: #00d4ff; font-size: 11px;")
        expand.setCursor(Qt.CursorShape.PointingHandCursor)
        expand.mousePressEvent = lambda _e: self.expand_workspace_requested.emit()
        layout.addWidget(expand, 0, Qt.AlignmentFlag.AlignRight)

        root.addWidget(panel)

    def _connect_state(self) -> None:
        self._state.assistant_state_changed.connect(self._on_assistant_state)

    def _on_assistant_state(self, state: str) -> None:
        self.orb.set_state(state)
        self.state_label.setText(state)

    def _position_bottom_right(self) -> None:
        screen = self.screen() or self.windowHandle().screen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        self.move(
            geo.right() - self.width() - 24,
            geo.bottom() - self.height() - 24,
        )

    # -- Interaction ---------------------------------------------------------------

    def _submit(self) -> None:
        text = self.command_input.text().strip()
        if text:
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
