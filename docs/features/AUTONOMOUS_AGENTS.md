# Autonomous Agents & Scheduler

JARVIS includes a bounded, PostgreSQL-backed agent runtime (`src/jarvis/agents/`) that executes safe, observable tasks without blocking the desktop UI.

---

## Built-In Agents (`src/jarvis/agents/builtin/`)

| Agent | Module | Role |
| :--- | :--- | :--- |
| **ChatAgent** | `chat_agent.py` | Conversational assistance |
| **CodingAgent** | `coding_agent.py` | Read-only/bounded coding assistance |
| **ProductivityAgent** | `productivity_agent.py` | Task & productivity workflows |
| **ResearchAgent** | `research_agent.py` | Information synthesis & local research |
| **SystemAgent** | `system_agent.py` | System status & safe local inspection |

---

## Execution, Planning & Scheduling

- **`AgentManager` (`manager.py`) & `AgentRunStore`:** Persists every run in `agent_runs` with strict state transitions (`VALID_AGENT_TRANSITIONS`), idempotency keys, output truncation (`_DEFAULT_MAX_OUTPUT_CHARS = 32_000`), and `EventBus` updates (`AgentRunStarted`, `AgentStepUpdated`, `AgentRunFinished`).
- **`Planner` (`planner.py`):** Decomposes multi-step tasks (`plan_models.py`).
- **`TaskScheduler` (`scheduler.py`):** Persistent, kill-switch-aware local job scheduler storing job definitions in `app_settings` (`agents.scheduled_jobs`) via `ScheduledJobStore`.
- **Kill Switch & Recovery:** Active runs respect `KillSwitchActiveError`; interrupted runs are handled by `agents/recovery.py`.
