# Application Lifecycle

JARVIS separates **window visibility** from **engine lifecycle**: hiding or closing a window never stops background services, active conversations, or the `EventBus`.

---

## Startup & Shutdown Sequence

```mermaid
sequenceDiagram
    participant Main as main.py
    participant Lock as SingleInstanceLock
    participant App as DesktopApplication
    participant Core as CoreEngine
    participant Reg as ServiceRegistry
    participant Bus as EventBus

    Main->>Lock: acquire() (Win32 Mutex)
    alt Already running
        Lock-->>Main: False -> Exit
    else Primary instance
        Main->>App: create(safe_mode, enforce_single_instance=True)
        App->>Core: start() via qasync QEventLoop
        Core->>Reg: Register & start services
        Core->>Bus: Publish StartupCompleted
        App-->>Main: Show initial window + Tray icon
    end

    Note over App,Bus: User closes HUD/Workspace/Floating -> Window hides only

    Main->>Bus: Tray Quit / Ctrl+Alt+Q -> ShutdownRequested
    Core->>Reg: Graceful stop (voice/vision release hardware, DB pool dispose)
    Core->>Bus: Publish ShutdownCompleted
    Main->>Lock: release()
```

---

## Key Components

- **`main.py`:** Parses `--safe-mode`, acquires `SingleInstanceLock`, creates `DesktopApplication.create(safe_mode=..., enforce_single_instance=True)`, and runs `qasync.QEventLoop`.
- **`CoreEngine` (`src/jarvis/core/engine.py`):** Coordinates `ServiceRegistry`, `LifecycleManager`, and `EventBus`. In `safe_mode`, skips optional hardware/agent modules for recovery.
- **`CrashRecoveryManager` (`src/jarvis/core/recovery.py`):** Tracks unclean shutdowns and assists safe recovery.
- **Independent Windows:** `WindowManager` (`src/jarvis/desktop/window_manager.py`) switches between HUD, Workspace, and Floating views without recreating `CoreEngine`.
