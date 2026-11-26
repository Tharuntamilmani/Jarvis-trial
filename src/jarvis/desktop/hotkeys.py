"""Windows global hotkey manager using RegisterHotKey + QAbstractNativeEventFilter (Phase 8.2)."""

from __future__ import annotations

from collections.abc import Callable
import ctypes
import ctypes.wintypes as wt
from typing import Any, Protocol

from PySide6.QtCore import QAbstractNativeEventFilter

from jarvis.monitoring.logging import get_logger

log = get_logger(__name__)

_WM_HOTKEY = 0x0312
_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_MOD_WIN = 0x0008
_MOD_NOREPEAT = 0x4000

_MODIFIER_MAP = {
    "alt": _MOD_ALT,
    "ctrl": _MOD_CONTROL,
    "control": _MOD_CONTROL,
    "shift": _MOD_SHIFT,
    "win": _MOD_WIN,
    "meta": _MOD_WIN,
}

_SPECIAL_KEYS = {
    "space": 0x20,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "esc": 0x1B,
    "escape": 0x1B,
}


class HotkeyParseError(ValueError):
    """Raised when a shortcut specification is invalid."""


def parse_hotkey(spec: str) -> tuple[int, int]:
    """Parse a shortcut like ``Ctrl+Shift+J`` into ``(win32_modifiers, vk)``."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if len(parts) < 2:
        raise HotkeyParseError(
            f"Hotkey {spec!r} must include at least one modifier and a key"
        )
    mods = _MOD_NOREPEAT
    for mod in parts[:-1]:
        flag = _MODIFIER_MAP.get(mod)
        if flag is None:
            raise HotkeyParseError(f"Unknown modifier {mod!r} in {spec!r}")
        mods |= flag

    key_str = parts[-1]
    if key_str in _SPECIAL_KEYS:
        vk = _SPECIAL_KEYS[key_str]
    elif len(key_str) == 1 and key_str.isalnum():
        vk = ord(key_str.upper())
    elif key_str.startswith("f") and key_str[1:].isdigit():
        fn = int(key_str[1:])
        if not 1 <= fn <= 24:
            raise HotkeyParseError(f"Invalid function key {key_str!r}")
        vk = 0x6F + fn
    else:
        raise HotkeyParseError(f"Unsupported key {key_str!r} in {spec!r}")
    return mods, vk


class HotkeyBackend(Protocol):
    """OS hotkey registration contract."""

    def register(self, hotkey_id: int, modifiers: int, vk: int) -> bool: ...

    def unregister(self, hotkey_id: int) -> bool: ...


class Win32HotkeyBackend:
    """Windows RegisterHotKey / UnregisterHotKey via user32."""

    def register(self, hotkey_id: int, modifiers: int, vk: int) -> bool:
        try:
            return bool(
                ctypes.windll.user32.RegisterHotKey(None, hotkey_id, modifiers, vk)
            )
        except Exception:  # noqa: BLE001
            return False

    def unregister(self, hotkey_id: int) -> bool:
        try:
            return bool(ctypes.windll.user32.UnregisterHotKey(None, hotkey_id))
        except Exception:  # noqa: BLE001
            return False


class GlobalHotkeyManager(QAbstractNativeEventFilter):
    """Manages global summon hotkeys with conflict & duplicate protection."""

    def __init__(self, backend: HotkeyBackend | None = None) -> None:
        super().__init__()
        self._backend = backend or Win32HotkeyBackend()
        self._next_id = 1
        self._by_name: dict[str, tuple[int, tuple[int, int]]] = {}
        self._callbacks: dict[int, Callable[[], Any]] = {}

    def register_hotkey(
        self, name: str, shortcut: str, callback: Callable[[], Any]
    ) -> bool:
        if not name or name in self._by_name:
            return False
        try:
            combo = parse_hotkey(shortcut)
        except HotkeyParseError as exc:
            log.warning("invalid hotkey %s (%s): %s", name, shortcut, exc)
            return False

        if any(existing_combo == combo for _, existing_combo in self._by_name.values()):
            log.warning("hotkey conflict for %s (%s)", name, shortcut)
            return False

        hid = self._next_id
        self._next_id += 1
        if not self._backend.register(hid, combo[0], combo[1]):
            log.warning("OS failed to register hotkey %s (%s)", name, shortcut)
            return False

        self._by_name[name] = (hid, combo)
        self._callbacks[hid] = callback
        return True

    def hotkey_id_for(self, name: str) -> int | None:
        entry = self._by_name.get(name)
        return entry[0] if entry is not None else None

    def dispatch_id(self, hotkey_id: int) -> bool:
        cb = self._callbacks.get(hotkey_id)
        if cb is None:
            return False
        cb()
        return True

    def unregister_hotkey(self, name: str) -> bool:
        entry = self._by_name.pop(name, None)
        if entry is None:
            return False
        hid, _ = entry
        self._callbacks.pop(hid, None)
        return self._backend.unregister(hid)

    def unregister_all(self) -> None:
        for name in list(self._by_name.keys()):
            self.unregister_hotkey(name)

    def nativeEventFilter(self, event_type, message) -> tuple[bool, int]:  # noqa: N802
        if event_type == b"windows_generic_MSG":
            msg = wt.MSG.from_address(int(message))
            if msg.message == _WM_HOTKEY:
                self.dispatch_id(int(msg.wParam))
                return (True, 0)
        return (False, 0)
