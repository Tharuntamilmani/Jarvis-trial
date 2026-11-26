"""UI & OS integration tests for Phase 8.6 Windows 11 Integration."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QRect, QSize
from PySide6.QtWidgets import QApplication

from jarvis.desktop.windows_integration import (
    WindowsStartupManager,
    clamp_window_to_screen,
)


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


class _FakeRunKeyBackend:
    def __init__(self) -> None:
        self.values: dict[str, str] = {"OtherApp": '"C:\\Other\\app.exe"'}

    def get_value(self, name: str) -> str | None:
        return self.values.get(name)

    def set_value(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete_value(self, name: str) -> bool:
        return self.values.pop(name, None) is not None


def test_windows_startup_registration_preserves_other_entries(tmp_path: Path) -> None:
    exe = tmp_path / "JARVIS.exe"
    exe.write_bytes(b"MZ")

    backend = _FakeRunKeyBackend()
    mgr = WindowsStartupManager(backend=backend)
    assert mgr.is_enabled() is False

    mgr.enable(exe)
    assert mgr.is_enabled() is True
    assert backend.values["JARVIS"] == f'"{exe}"'
    assert "OtherApp" in backend.values

    # Rejects missing or unsafe executable paths
    with pytest.raises(ValueError):
        mgr.enable(tmp_path / "missing.exe")

    assert mgr.disable() is True
    assert mgr.is_enabled() is False
    # Other registry entries remain untouched
    assert backend.values == {"OtherApp": '"C:\\Other\\app.exe"'}


def test_multi_monitor_clamp_window_to_screen() -> None:
    _qapp()
    screen_geo = QRect(1920, 0, 1920, 1080)  # secondary monitor
    pos = clamp_window_to_screen(screen_geo, QSize(300, 480))
    assert pos.x() == 1920 + 1920 - 300 - 24
    assert pos.y() == 1080 - 480 - 24
