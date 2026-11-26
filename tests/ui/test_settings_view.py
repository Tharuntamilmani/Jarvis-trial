"""UI tests for Phase 8.1 native PySide6 SettingsView."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication, QLineEdit

from jarvis.config import Settings
from jarvis.config.preferences import PreferencesService
from jarvis.desktop.viewmodels.app_state import AppState
from jarvis.desktop.views.settings_view import SettingsView
from jarvis.desktop.windows.workspace_window import WorkspaceWindow
from jarvis.security.credentials import CredentialStore


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_settings_view_never_exposes_secret_and_embeds_voice_vision(
    tmp_path: Path,
) -> None:
    _qapp()
    state = AppState()
    settings = Settings(
        log_dir=tmp_path / "logs",
        audit_log_path=tmp_path / "logs" / "audit.log",
    )
    creds = CredentialStore(settings)
    pref_svc = PreferencesService(database=None, credentials=creds)

    view = SettingsView(state, preferences_service=pref_svc)
    assert view.voice_controls is not None
    assert view.vision_controls is not None
    assert view.api_key_input.echoMode() == QLineEdit.EchoMode.Password
    assert view.api_key_status_label.text() == "Not configured"

    # Store API key via UI; UI shows "Configured" and clears input without exposing secret
    view.api_key_input.setText("gsk_ui_secret_99999")
    view.save_key_btn.click()
    assert view.api_key_status_label.text() == "Configured"
    assert view.api_key_input.text() == ""
    assert creds.load_secret("groq_api_key") == "gsk_ui_secret_99999"

    # Delete API key via UI
    view.delete_key_btn.click()
    assert view.api_key_status_label.text() == "Not configured"
    assert creds.load_secret("groq_api_key") is None

    # WorkspaceWindow hosts SettingsView cleanly
    ws = WorkspaceWindow(state)
    assert ws.settings_view is not None
    assert ws.voice_controls is not None
    assert ws.vision_controls is not None
