"""Execution plan models and budgets for Phase 4.2 TaskPlanner."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum


class PlanStepStatus(StrEnum):
    """Lifecycle status of one step in an ``ExecutionPlan``."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class PlanBudget:
    """Hard execution limits enforced by ``TaskPlanner``."""

    max_steps: int = 16
    max_duration_seconds: float = 120.0
    max_output_chars: int = 32_000
    max_concurrency: int = 2


@dataclass(frozen=True)
class PlanStep:
    """One node in a dependency-aware execution DAG."""

    step_id: str
    agent_id: str
    input_text: str
    depends_on: tuple[str, ...] = ()
    retryable: bool = False
    max_retries: int = 0
    requires_approval: bool = False


@dataclass
class PlanCheckpoint:
    """Mutable checkpoint capturing completed steps for recovery."""

    plan_id: str
    completed_outputs: dict[str, str] = field(default_factory=dict)
    step_statuses: dict[str, PlanStepStatus] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionPlan:
    """Validated goal decomposition into a DAG of ``PlanStep``s."""

    goal: str
    steps: tuple[PlanStep, ...]
    plan_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    budget: PlanBudget = field(default_factory=PlanBudget)


@dataclass(frozen=True)
class PlanResult:
    """Final outcome of executing an ``ExecutionPlan``."""

    plan_id: str
    status: PlanStepStatus
    step_outputs: dict[str, str]
    step_statuses: dict[str, PlanStepStatus]
    error: str | None = None
