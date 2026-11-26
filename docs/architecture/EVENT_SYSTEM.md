# Event System

Modules communicate asynchronously through `EventBus` (`src/jarvis/core/event_bus.py`) using frozen dataclass events defined in `src/jarvis/core/events.py`.

---

## Event Contract

- Every event subclasses `Event`, a `@dataclass(frozen=True)` with an automatic monotonic `timestamp`.
- `event.name` converts the class name to `snake_case` (for example, `ServiceStatusChanged` → `service_status_changed`).
- `event.as_dict()` serializes payload fields without internal timestamps.

---

## Core Event Categories

| Category | Verified Events (`src/jarvis/core/events.py`) | Purpose |
| :--- | :--- | :--- |
| **Lifecycle** | `StartupCompleted`, `ShutdownRequested`, `ShutdownCompleted` | Engine startup and tray/hotkey shutdown coordination |
| **Services** | `ServiceStatusChanged` | Reports `healthy`, `degraded`, `unavailable`, `starting`, or `stopped` |
| **Security** | `KillSwitchTriggered`, approval & audit events | Emergency stop and security state notifications |
| **Intelligence** | `ProviderSelected`, `CloudFallbackProposed`, `CloudConsentDecided` | Local/cloud routing and per-request consent flow |
| **Agents** | `AgentRunStarted`, `AgentStepUpdated`, `AgentRunFinished` | Real-time agent progress updates for UI & logs |
| **Voice / Vision** | Voice activity, transcription, camera/presence events | Hardware state updates without coupling UI to drivers |

---

## Design Guarantees

- **No Qt in Core:** `EventBus` uses `asyncio` only; the desktop layer bridges events into Qt widgets via `qasync`.
- **Decoupled Subscribers:** Publishers never depend on whether HUD, Workspace, or Floating windows are currently visible.
