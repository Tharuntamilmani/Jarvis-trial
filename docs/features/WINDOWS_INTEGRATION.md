# Windows 11 Integration

JARVIS integrates directly with Windows 11 desktop APIs (`src/jarvis/desktop/`, `src/jarvis/core/single_instance.py`).

---

## Features

- **Global Hotkeys (`src/jarvis/desktop/hotkeys.py`):** Uses Win32 `RegisterHotKey` + `QAbstractNativeEventFilter` (`_WM_HOTKEY = 0x0312`) for system-wide shortcuts (`Ctrl+Shift+J`, `Ctrl+Shift+F`, and `Ctrl+Alt+Q`), detects registration conflicts, and unregisters cleanly on exit.
- **System Tray (`src/jarvis/desktop/tray.py`):** Provides mode switching (HUD, Workspace, Floating), **Emergency Stop** (kill switch), notifications, and **Quit**.
- **Optional Startup Registration (`src/jarvis/desktop/windows_integration.py`):** `WindowsStartupManager` (`Win32RunKeyBackend`) registers/unregisters `JARVIS` under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` (user-scoped; never requires admin or `HKLM`).
- **Multi-Monitor & DPI Awareness:** Positioning helpers clamp windows safely across displays and DPI scaling factors.
- **Single-Instance Lock (`src/jarvis/core/single_instance.py`):** Win32 mutex prevents duplicate background engines.
