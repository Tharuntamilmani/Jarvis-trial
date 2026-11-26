"""Full-screen futuristic HUD window."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from jarvis.desktop.common.orb import OrbWidget
from jarvis.desktop.common.status_chip import StatusChip
from jarvis.desktop.viewmodels.app_state import AppState
from jarvis.desktop.viewmodels.vision_controller import VisionController
from jarvis.desktop.viewmodels.voice_controller import VoiceController
from jarvis.desktop.views.vision_controls import VisionControlsPanel
from jarvis.desktop.views.voice_controls import VoiceControlsPanel


class HudWindow(QWidget):
    """Full-screen HUD mode. Hiding preserves state; closing never touches
    the engine (the window manager handles lifecycle)."""

    hide_requested = Signal()
    switch_workspace_requested = Signal()
    switch_floating_requested = Signal()

    def __init__(
        self,
        state: AppState,
        voice_controller: VoiceController | None = None,
        vision_controller: VisionController | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window,
        )
        self.setWindowTitle("JARVIS — HUD")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setObjectName("HudRoot")
        self._state = state
        self._voice = voice_controller
        self._vision = vision_controller
        self._chips: dict[str, StatusChip] = {}

        self._build_ui()
        self._connect_state()
        self._mirror_current_state()

    # -- UI ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(44, 32, 44, 28)
        root.setSpacing(16)

        # Top telemetry header bar
        top_bar = QHBoxLayout()
        top_bar.setSpacing(12)

        brand_col = QVBoxLayout()
        brand_col.setSpacing(2)
        title = QLabel("JARVIS")
        title.setObjectName("HudTitle")
        subtitle = QLabel("Native Windows AI Assistant")
        subtitle.setObjectName("HudSubtitle")
        brand_col.addWidget(title)
        brand_col.addWidget(subtitle)
        top_bar.addLayout(brand_col)
        top_bar.addStretch(1)

        self.telemetry_label = QLabel("CPU 0.0%  |  RAM 0.0%  |  DISK 0.0%")
        self.telemetry_label.setObjectName("HudTelemetry")
        self.telemetry_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top_bar.addWidget(self.telemetry_label, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(top_bar)

        root.addStretch(1)

        # Central focal orb + state readout
        self.orb = OrbWidget()
        self.orb.setFixedSize(280, 280)
        orb_row = QHBoxLayout()
        orb_row.addStretch(1)
        orb_row.addWidget(self.orb)
        orb_row.addStretch(1)
        root.addLayout(orb_row)

        self.assistant_label = QLabel("IDLE")
        self.assistant_label.setObjectName("HudSubtitle")
        self.assistant_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.assistant_label)

        # Compact centered hardware controls pill
        controls_card = QFrame()
        controls_card.setObjectName("GlassPanel")
        controls_layout = QVBoxLayout(controls_card)
        controls_layout.setContentsMargins(16, 10, 16, 10)
        controls_layout.setSpacing(8)
        self.voice_panel = VoiceControlsPanel(
            self._state, self._voice, compact=True
        )
        controls_layout.addWidget(self.voice_panel)
        self.vision_panel = VisionControlsPanel(
            self._state, self._vision, compact=True
        )
        controls_layout.addWidget(self.vision_panel)
        controls_row = QHBoxLayout()
        controls_row.addStretch(1)
        controls_row.addWidget(controls_card)
        controls_row.addStretch(1)
        root.addLayout(controls_row)

        root.addStretch(1)

        # Two-column bottom dock: System Status + Agent Activity
        dock = QHBoxLayout()
        dock.setSpacing(16)

        panel = QFrame()
        panel.setObjectName("GlassPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 14, 18, 14)
        panel_layout.setSpacing(10)
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
        dock.addWidget(panel, 3)

        agent_panel = QFrame()
        agent_panel.setObjectName("GlassPanel")
        agent_layout = QVBoxLayout(agent_panel)
        agent_layout.setContentsMargins(18, 14, 18, 14)
        agent_layout.setSpacing(6)
        agent_title = QLabel("AGENT ACTIVITY")
        agent_title.setObjectName("SectionTitle")
        agent_layout.addWidget(agent_title)
        self.agent_status_label = QLabel("No active agents")
        self.agent_status_label.setObjectName("HudSubtitle")
        agent_layout.addWidget(self.agent_status_label)
        self.agent_timeline = QListWidget()
        self.agent_timeline.setMaximumHeight(68)
        agent_layout.addWidget(self.agent_timeline)
        dock.addWidget(agent_panel, 2)

        root.addLayout(dock)

        hint = QLabel("Esc Hide   ·   W Workspace   ·   F Floating")
        hint.setObjectName("HudSubtitle")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(hint)

    def _connect_state(self) -> None:
        self._state.service_status_changed.connect(self._on_service_status)
        self._state.assistant_state_changed.connect(self._on_assistant_state)
        self._state.kill_switch_changed.connect(self._on_kill_switch)
        self._state.agent_run_started.connect(lambda *_: self._refresh_agents())
        self._state.agent_step_updated.connect(lambda *_: self._refresh_agents())
        self._state.agent_run_finished.connect(lambda *_: self._refresh_agents())
        self._state.voice_levels_updated.connect(self._on_voice_levels)
        self._state.voice_state_changed.connect(self._on_voice_state)
        self._state.telemetry_updated.connect(self._on_telemetry_updated)

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
        self._refresh_agents()

    def _refresh_agents(self) -> None:
        active = list(self._state.active_agents.values())
        if active:
            cur = active[0]
            self.agent_status_label.setText(
                f"{cur.get('agent_id', 'agent')}: {cur.get('step', 'running')}"
            )
        else:
            self.agent_status_label.setText("No active agents")
        self.agent_timeline.clear()
        for entry in self._state.recent_agent_activity[:5]:
            QListWidgetItem(
                f"{entry['agent_id']} · {entry['status']} "
                f"({entry.get('duration_ms', 0)} ms)",
                self.agent_timeline,
            )

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
        elif self._state.assistant_state:
            self.orb.set_state(self._state.assistant_state)

    def _on_telemetry_updated(self, snap: dict) -> None:
        cpu = float(snap.get("cpu_percent", 0.0))
        ram = float(snap.get("ram_percent", 0.0))
        disk = float(snap.get("disk_percent", 0.0))
        gpu = snap.get("gpu_percent")
        gpu_part = f"  ·  GPU {gpu:.1f}%" if gpu is not None else ""
        self.telemetry_label.setText(
            f"CPU {cpu:.1f}%  ·  RAM {ram:.1f}%  ·  DISK {disk:.1f}%{gpu_part}"
        )

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
