"""UI tests for Phase 8.2 Global Hotkeys, TrayService & Toast Notifications."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from jarvis.desktop.hotkeys import GlobalHotkeyManager, HotkeyParseError, parse_hotkey
from jarvis.desktop.tray import TrayService
from jarvis.desktop.viewmodels.app_state import AppState
from jarvis.desktop.window_manager import WindowManager


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


class _FakeHotkeyBackend:
    def __init__(self, *, fail_register: bool = False) -> None:
        self.fail_register = fail_register
        self.registered: dict[int, tuple[int, int]] = {}
        self.unregistered: list[int] = []

    def register(self, hotkey_id: int, modifiers: int, vk: int) -> bool:
        if self.fail_register:
            return False
        self.registered[hotkey_id] = (modifiers, vk)
        return True

    def unregister(self, hotkey_id: int) -> bool:
        self.unregistered.append(hotkey_id)
        return self.registered.pop(hotkey_id, None) is not None


def test_parse_hotkey_validation() -> None:
    mods, vk = parse_hotkey("Ctrl+Shift+J")
    assert mods != 0
    assert vk == ord("J")

    # Missing modifier or invalid key rejected
    with pytest.raises(HotkeyParseError):
        parse_hotkey("J")
    with pytest.raises(HotkeyParseError):
        parse_hotkey("")


def test_global_hotkey_manager_conflicts_duplicates_and_cleanup() -> None:
    _qapp()
    backend = _FakeHotkeyBackend()
    mgr = GlobalHotkeyManager(backend=backend)
    triggered: list[str] = []

    assert mgr.register_hotkey("summon_hud", "Ctrl+Shift+J", lambda: triggered.append("hud")) is True
    # Duplicate name or conflicting shortcut rejected
    assert mgr.register_hotkey("summon_hud", "Ctrl+Shift+K", lambda: None) is False
    assert mgr.register_hotkey("other", "Ctrl+Shift+J", lambda: None) is False

    # Simulate WM_HOTKEY dispatch
    hid = mgr.hotkey_id_for("summon_hud")
    assert hid is not None
    assert mgr.dispatch_id(hid) is True
    assert triggered == ["hud"]

    # Shutdown unregisters all hotkeys cleanly
    mgr.unregister_all()
    assert backend.registered == {}
    assert hid in backend.unregistered

    # OS registration failure handled gracefully
    failing = GlobalHotkeyManager(backend=_FakeHotkeyBackend(fail_register=True))
    assert failing.register_hotkey("hud", "Ctrl+Shift+H", lambda: None) is False


def test_tray_service_notifications_and_quick_toggles(monkeypatch) -> None:
    _qapp()
    state = AppState()
    wm = WindowManager(state)
    tray = TrayService(wm)

    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(
        tray._tray,  # noqa: SLF001
        "showMessage",
        lambda title, msg, *_args: shown.append((title, msg)),
    )

    tray.notify_event("warning", "Approval requested: fs.delete")
    assert len(shown) == 1
    assert "Approval requested" in shown[0][1]

    voice_clicks: list[bool] = []
    cam_clicks: list[bool] = []
    tray.voice_toggle_requested.connect(lambda: voice_clicks.append(True))
    tray.camera_toggle_requested.connect(lambda: cam_clicks.append(True))
    tray.voice_toggle_requested.emit()
    tray.camera_toggle_requested.emit()
    assert voice_clicks == [True]
    assert cam_clicks == [True]
