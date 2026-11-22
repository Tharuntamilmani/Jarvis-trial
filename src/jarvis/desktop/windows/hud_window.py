"""Full-screen futuristic HUD window."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from jarvis.desktop.common.orb import OrbWidget
from jarvis.desktop.common.status_chip import StatusChip
from jarvis.desktop.viewmodels.app_state import AppState


class HudWindow(QWidget):
    """Full-screen HUD mode. Hiding preserves state; closing never touches
    the engine (the window manager handles lifecycle)."""

    hide_requested = Signal()
    switch_workspace_requested = Signal()
    switch_floating_requested = Signal()

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window,
        )
        self.setWindowTitle("JARVIS — HUD")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setObjectName("HudRoot")
        self._state = state
        self._chips: dict[str, StatusChip] = {}

        self._build_ui()
        self._connect_state()
        self._mirror_current_state()

    # -- UI ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 32, 40, 32)
        root.setSpacing(18)

        title = QLabel("J A R V I S")
        title.setObjectName("HudTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        subtitle = QLabel("NATIVE AI DESKTOP ASSISTANT")
        subtitle.setObjectName("HudSubtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)

        root.addWidget(title)
        root.addWidget(subtitle)
        root.addStretch(1)

        self.orb = OrbWidget()
        self.orb.setFixedSize(260, 260)
        orb_row = QHBoxLayout()
        orb_row.addStretch(1)
        orb_row.addWidget(self.orb)
        orb_row.addStretch(1)
        root.addLayout(orb_row)

        self.assistant_label = QLabel("IDLE")
        self.assistant_label.setObjectName("HudSubtitle")
        self.assistant_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.assistant_label)
        root.addStretch(1)

        # Status panel
        panel = QFrame()
        panel.setObjectName("GlassPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(16, 10, 16, 10)
        panel_title = QLabel("SYSTEM STATUS")
        panel_title.setObjectName("SectionTitle")
        panel_layout.addWidget(panel_title)

        chips_row = QHBoxLayout()
        chips_row.setSpacing(10)
        for service in ("engine", "database", "providers", "kill_switch"):
            chip = StatusChip(service)
            self._chips[service] = chip
            chips_row.addWidget(chip)
        chips_row.addStretch(1)
        panel_layout.addLayout(chips_row)
        root.addWidget(panel)

        hint = QLabel("ESC hide  ·  W workspace  ·  F floating")
        hint.setObjectName("HudSubtitle")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(hint)

    def _connect_state(self) -> None:
        self._state.service_status_changed.connect(self._on_service_status)
        self._state.assistant_state_changed.connect(self._on_assistant_state)
        self._state.kill_switch_changed.connect(self._on_kill_switch)

    def _mirror_current_state(self) -> None:
        """Hydrate indicators that were published before this window existed."""
        for service, chip in self._chips.items():
            status = self._state.service_statuses.get(service)
            if status is not None:
                chip.set_status(
                    status, self._state.service_details.get(service, "")
                )
        if self._state.kill_switch_active:
            self._on_kill_switch(True)
        else:
            self._on_assistant_state(self._state.assistant_state or "idle")

    def _on_service_status(self, service: str, status: str, detail: str) -> None:
        key = service if service in self._chips else None
        if key is None:
            if service in ("ollama", "groq"):
                key = "providers"
            else:
                return
        self._chips[key].set_status(status, detail)

    def _on_kill_switch(self, active: bool) -> None:
        if active:
            self.orb.set_state("kill_switch")
            self.assistant_label.setText("EMERGENCY STOP ENGAGED")
            return
        self._on_assistant_state(self._state.assistant_state or "idle")

    def _on_assistant_state(self, state: str) -> None:
        if self._state.kill_switch_active:
            return
        self.orb.set_state(state)
        self.assistant_label.setText(state.upper())

    # -- Key handling ------------------------------------------------------------

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.hide_requested.emit()
            self.hide()
            return
        if key == Qt.Key.Key_W:
            self.switch_workspace_requested.emit()
            return
        if key == Qt.Key.Key_F:
            self.switch_floating_requested.emit()
            return
        super().keyPressEvent(event)

    def show_fullscreen(self) -> None:
        self.showFullScreen()
        self.activateWindow()
