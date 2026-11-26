# First Launch

## Starting JARVIS

From the repository root with your virtual environment activated:

```powershell
python main.py
```

To start in **Safe Mode** (skips optional hardware/agent initialization for recovery or troubleshooting):

```powershell
python main.py --safe-mode
```

### Single-Instance Behavior

`main.py` acquires a Win32 mutex (`SingleInstanceLock`) before initializing the UI. If another JARVIS instance is already running, the second launch exits rather than starting a duplicate background engine.

---

## The Three Desktop Modes

All three windows share one `CoreEngine`, `ApplicationState`, and PostgreSQL session. Switching modes hides or shows windows without restarting the AI engine:

1. **Premium Workspace** — Full desktop workspace with streaming chat, conversation history, memory management, autonomous agents, settings, security controls, and diagnostics.
2. **Full-Screen HUD** — Futuristic full-screen overlay featuring the animated central orb, voice activity visualization, system monitoring, notifications, and agent activity.
3. **Floating Assistant** — Always-on-top compact orb window with quick text commands, voice activation, and expand/minimize controls.

> [!NOTE]
> Closing a window hides it rather than terminating JARVIS. To exit cleanly, use **Quit** from the system tray icon or press the quit hotkey (`Ctrl+Alt+Q` by default).

---

## Default Global Hotkeys

| Shortcut | Default Action | Config Setting |
| :--- | :--- | :--- |
| `Ctrl+Shift+J` | Toggle / focus HUD or main assistant window | `Hotkeys` (`src/jarvis/desktop/hotkeys.py`) |
| `Ctrl+Shift+F` | Toggle Floating Assistant window | `Hotkeys` (`src/jarvis/desktop/hotkeys.py`) |
| `Ctrl+Alt+Q` | Quit JARVIS cleanly | `JARVIS_HOTKEY_QUIT` (`Settings.hotkey_quit`) |

---

## First-Run Safety Checks

- **Camera:** Disabled by default on startup; requires explicit user activation.
- **Cloud AI:** Disabled unless a `groq_api_key` is stored in DPAPI **and** you approve each request individually via the inline consent card.
- **Tool Execution:** High-risk actions fail closed and require an explicit single-use approval in the approval overlay.
