"""Value types and state-transition rules for Phase 4 agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class AgentStatus(StrEnum):
    """Lifecycle states of an agent run."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"


TERMINAL_AGENT_STATUSES = frozenset(
    {
        AgentStatus.COMPLETED,
        AgentStatus.FAILED,
        AgentStatus.CANCELLED,
        AgentStatus.TIMED_OUT,
        AgentStatus.INTERRUPTED,
    }
)

VALID_AGENT_TRANSITIONS: dict[AgentStatus, frozenset[AgentStatus]] = {
    AgentStatus.QUEUED: frozenset(
        {
            AgentStatus.RUNNING,
            AgentStatus.CANCELLED,
            AgentStatus.FAILED,
            AgentStatus.INTERRUPTED,
        }
    ),
    AgentStatus.RUNNING: TERMINAL_AGENT_STATUSES,
    AgentStatus.COMPLETED: frozenset(),
    AgentStatus.FAILED: frozenset(),
    AgentStatus.CANCELLED: frozenset(),
    AgentStatus.TIMED_OUT: frozenset(),
    AgentStatus.INTERRUPTED: frozenset(),
}


class AgentErrorCategory(StrEnum):
    """Safe, machine-readable failure category for agent runs."""

    VALIDATION_ERROR = "validation_error"
    EXECUTION_ERROR = "execution_error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    KILL_SWITCH = "kill_switch"
    PERSISTENCE_ERROR = "persistence_error"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class AgentContext:
    """Execution context supplied to ``BaseAgent.run()``."""

    run_id: str
    agent_id: str
    input_text: str
    session_id: str = ""
    parent_run_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentResult:
    """Structured output returned by ``BaseAgent.run()``."""

    output: str
    status: AgentStatus = AgentStatus.COMPLETED
    error_category: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentRunRecord:
    """Persisted agent run snapshot from PostgreSQL ``agent_runs``."""

    id: str
    agent: str
    input: str
    output: str | None
    status: AgentStatus
    duration_ms: int | None
    idempotency_key: str | None
    error_category: str | None
    owner_session_id: str | None
    parent_run_id: str | None
    created_at: datetime
    updated_at: datetime
