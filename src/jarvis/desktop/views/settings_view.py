"""Native PySide6 unified Settings & DPAPI Credential Manager (Phase 8.1)."""

from __future__ import annotations

import asyncio
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from jarvis.config.preferences import PreferencesService, UserAppPreferences
from jarvis.desktop.viewmodels.app_state import AppState
from jarvis.desktop.viewmodels.vision_controller import VisionController
from jarvis.desktop.viewmodels.voice_controller import VoiceController
from jarvis.desktop.views.vision_controls import VisionControlsPanel
from jarvis.desktop.views.voice_controls import VoiceControlsPanel
from jarvis.monitoring.diagnostics import DiagnosticsService


class SettingsView(QWidget):
    """Unified Settings view: preferences, DPAPI credentials, voice, vision."""

    def __init__(
        self,
        state: AppState,
        *,
        voice_controller: VoiceController | None = None,
        vision_controller: VisionController | None = None,
        preferences_service: PreferencesService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._voice = voice_controller
        self._vision = vision_controller
        self._prefs_svc = preferences_service
        self._tasks: set[asyncio.Task[Any]] = set()
        self._build_ui()
        self._refresh_credential_status()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        heading = QLabel("SETTINGS, PREFERENCES & CREDENTIALS")
        heading.setObjectName("SectionTitle")
        layout.addWidget(heading)

        # General, Models, Appearance, Shortcuts, Storage
        form_panel = QFrame()
        form_panel.setObjectName("GlassPanel")
        form = QFormLayout(form_panel)
        form.setContentsMargins(16, 14, 16, 14)
        form.setSpacing(10)

        self.startup_check = QCheckBox("Start JARVIS with Windows")
        form.addRow("General", self.startup_check)

        self.provider_combo = QComboBox()
        self.provider_combo.addItems(["auto", "ollama", "groq"])
        form.addRow("Default Provider", self.provider_combo)

        self.language_input = QLineEdit("en")
        form.addRow("Language", self.language_input)

        self.ollama_model_input = QLineEdit("qwen3:8b")
        form.addRow("Ollama Model", self.ollama_model_input)

        self.groq_model_input = QLineEdit("llama-3.3-70b-versatile")
        form.addRow("Groq Model", self.groq_model_input)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["dark", "midnight"])
        form.addRow("Theme", self.theme_combo)

        self.hud_hotkey_input = QLineEdit("Ctrl+Shift+J")
        form.addRow("HUD Hotkey", self.hud_hotkey_input)

        self.floating_hotkey_input = QLineEdit("Ctrl+Shift+F")
        form.addRow("Floating Hotkey", self.floating_hotkey_input)

        self.retention_spin = QSpinBox()
        self.retention_spin.setRange(1, 365)
        self.retention_spin.setValue(14)
        form.addRow("Backup Retention (days)", self.retention_spin)

        save_row = QHBoxLayout()
        self.save_prefs_btn = QPushButton("Save Preferences")
        self.save_prefs_btn.clicked.connect(self._on_save_prefs)
        save_row.addWidget(self.save_prefs_btn)
        self.prefs_status_label = QLabel("")
        self.prefs_status_label.setStyleSheet("color: #7f95b2; font-size: 11px;")
        save_row.addWidget(self.prefs_status_label, 1)
        form.addRow("", save_row)
        layout.addWidget(form_panel)

        # DPAPI Credentials (masked, never displays stored secret)
        cred_panel = QFrame()
        cred_panel.setObjectName("GlassPanel")
        cred_layout = QVBoxLayout(cred_panel)
        cred_layout.setContentsMargins(16, 12, 16, 12)
        cred_layout.setSpacing(8)
        cred_title = QLabel("DPAPI CREDENTIAL MANAGER (GROQ API KEY)")
        cred_title.setObjectName("SectionTitle")
        cred_layout.addWidget(cred_title)

        cred_row = QHBoxLayout()
        self.api_key_status_label = QLabel("Not configured")
        cred_row.addWidget(self.api_key_status_label)
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText("Enter API key (stored encrypted via DPAPI)…")
        cred_row.addWidget(self.api_key_input, 1)
        self.save_key_btn = QPushButton("Save / Replace")
        self.save_key_btn.clicked.connect(self._on_save_api_key)
        cred_row.addWidget(self.save_key_btn)
        self.delete_key_btn = QPushButton("Delete")
        self.delete_key_btn.clicked.connect(self._on_delete_api_key)
        cred_row.addWidget(self.delete_key_btn)
        cred_layout.addLayout(cred_row)
        layout.addWidget(cred_panel)

        # Voice & Vision controls
        self.voice_controls = VoiceControlsPanel(
            self._state, self._voice, compact=False
        )
        layout.addWidget(self.voice_controls)

        self.vision_controls = VisionControlsPanel(
            self._state, self._vision, compact=False
        )
        layout.addWidget(self.vision_controls)

        diag_row = QHBoxLayout()
        self.export_diag_btn = QPushButton("Export Privacy-Safe Diagnostics")
        self.export_diag_btn.clicked.connect(self._on_export_diagnostics)
        diag_row.addWidget(self.export_diag_btn)
        diag_row.addStretch(1)
        layout.addLayout(diag_row)
        layout.addStretch(1)

        scroll.setWidget(container)
        outer.addWidget(scroll)

    def _refresh_credential_status(self) -> None:
        if self._prefs_svc is None:
            self.api_key_status_label.setText("Unavailable")
            return
        self.api_key_status_label.setText(
            self._prefs_svc.credential_status("groq_api_key")
        )

    def _on_save_api_key(self) -> None:
        if self._prefs_svc is None:
            return
        raw = self.api_key_input.text()
        self.api_key_input.clear()
        try:
            self._prefs_svc.set_api_key("groq_api_key", raw)
        except Exception as exc:  # noqa: BLE001
            self.prefs_status_label.setText(f"Credential error: {exc}")
            return
        self._refresh_credential_status()
        self.prefs_status_label.setText("API key saved in Windows DPAPI")

    def _on_delete_api_key(self) -> None:
        if self._prefs_svc is None:
            return
        self._prefs_svc.delete_api_key("groq_api_key")
        self._refresh_credential_status()

    def _on_save_prefs(self) -> None:
        if self._prefs_svc is None:
            self.prefs_status_label.setText("Preferences service unavailable")
            return
        try:
            prefs = UserAppPreferences(
                startup_with_windows=self.startup_check.isChecked(),
                default_provider=self.provider_combo.currentText(),  # type: ignore[arg-type]
                language=self.language_input.text().strip() or "en",
                ollama_model=self.ollama_model_input.text().strip() or "qwen3:8b",
                groq_model=self.groq_model_input.text().strip() or "llama-3.3-70b-versatile",
                theme=self.theme_combo.currentText(),  # type: ignore[arg-type]
                summon_hud_hotkey=self.hud_hotkey_input.text().strip() or "Ctrl+Shift+J",
                toggle_floating_hotkey=self.floating_hotkey_input.text().strip() or "Ctrl+Shift+F",
                backup_retention=self.retention_spin.value(),
            )
        except Exception as exc:  # noqa: BLE001
            self.prefs_status_label.setText(f"Invalid setting: {exc}")
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.prefs_status_label.setText("Event loop unavailable")
            return
        task = loop.create_task(self._persist_prefs(prefs))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _persist_prefs(self, prefs: UserAppPreferences) -> None:
        if self._prefs_svc is None:
            return
        try:
            await self._prefs_svc.save(prefs)
            self.prefs_status_label.setText("Preferences saved")
        except Exception as exc:  # noqa: BLE001
            self.prefs_status_label.setText(f"Save failed: {exc}")

    def _on_export_diagnostics(self) -> None:
        svc = DiagnosticsService()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.prefs_status_label.setText("Diagnostic export ready")
            return
        task = loop.create_task(self._export_diagnostics_async(svc))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _export_diagnostics_async(self, svc: DiagnosticsService) -> None:
        from pathlib import Path

        report = await svc.collect()
        out = svc.export_report(report, Path("logs") / "diagnostics_report.json")
        self.prefs_status_label.setText(f"Diagnostics exported: {out.name}")
