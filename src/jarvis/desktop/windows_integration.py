"""Windows 11 startup registration & multi-monitor DPI helpers (Phase 8.6)."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol
import winreg

from PySide6.QtCore import QPoint, QRect, QSize

_RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "JARVIS"


class StartupRegistryBackend(Protocol):
    """Abstraction over HKCU\\...\\Run for unit-testable startup registration."""

    def get_value(self, name: str) -> str | None: ...

    def set_value(self, name: str, value: str) -> None: ...

    def delete_value(self, name: str) -> bool: ...


class Win32RunKeyBackend:
    """User-scoped Windows Run key (never requires admin or HKLM)."""

    def get_value(self, name: str) -> str | None:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH, 0, winreg.KEY_READ
            ) as key:
                val, _ = winreg.QueryValueEx(key, name)
                return str(val)
        except FileNotFoundError:
            return None

    def set_value(self, name: str, value: str) -> None:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)

    def delete_value(self, name: str) -> bool:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, name)
                return True
        except FileNotFoundError:
            return False


class WindowsStartupManager:
    """Optional, reversible HKCU startup-with-Windows registration."""

    def __init__(
        self,
        backend: StartupRegistryBackend | None = None,
        *,
        value_name: str = _VALUE_NAME,
    ) -> None:
        self._backend = backend or Win32RunKeyBackend()
        self._name = value_name

    def is_enabled(self) -> bool:
        return self._backend.get_value(self._name) is not None

    def enable(self, executable_path: Path) -> None:
        exe = Path(executable_path)
        if not exe.exists() or not exe.is_file():
            raise ValueError(f"Executable does not exist: {exe}")
        resolved = exe.resolve()
        self._backend.set_value(self._name, f'"{resolved}"')

    def disable(self) -> bool:
        return self._backend.delete_value(self._name)


def clamp_window_to_screen(
    available_geometry: QRect,
    window_size: QSize,
    *,
    margin: int = 24,
) -> QPoint:
    """Compute monitor-aware bottom-right position clamped inside screen bounds."""
    x = available_geometry.right() - window_size.width() - margin + 1
    y = available_geometry.bottom() - window_size.height() - margin + 1
    x = max(available_geometry.left() + margin, x)
    y = max(available_geometry.top() + margin, y)
    return QPoint(x, y)
