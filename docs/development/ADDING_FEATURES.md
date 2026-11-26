# Adding Features

Follow these extension patterns to keep JARVIS modular and compliant with [AGENTS.md](../../AGENTS.md).

---

## Adding an Event

1. Define a `@dataclass(frozen=True)` subclass of `Event` in `src/jarvis/core/events.py`.
2. Publish via `await bus.publish(MyEvent(...))` and subscribe via `EventBus`.

## Adding a Tool

1. Implement a typed tool handler and input/output schema in `src/jarvis/tools/`.
2. Register it in `ToolRegistry` with an explicit `RiskLevel` and `Capability` (`src/jarvis/security/permissions.py`).
3. Ensure high-risk actions require `ApprovalService` single-use grants and log through `AuditService`.

## Adding a Built-In Agent

1. Subclass `BaseAgent` (`src/jarvis/agents/base_agent.py`) under `src/jarvis/agents/builtin/`.
2. Register the agent in `AgentRegistry` (`src/jarvis/agents/registry.py`) so `AgentManager` can track runs in `agent_runs`.

## Adding a Desktop View

1. Build a native `QWidget` under `src/jarvis/desktop/views/` using QSS and `QPropertyAnimation`.
2. Connect to backend services via `ApplicationState` and `EventBus` — never embed business logic directly in widgets.
