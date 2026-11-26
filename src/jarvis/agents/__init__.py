"""Autonomous agent runtime (Phase 4). Core-only: never imports PySide6."""

from jarvis.agents.base_agent import (
    AgentCancelledError,
    AgentCapacityError,
    AgentDuplicateError,
    AgentError,
    AgentNotFoundError,
    AgentTransitionError,
    BaseAgent,
)
from jarvis.agents.capabilities import (
    DelegationCoordinator,
    DelegationError,
    DelegationPolicy,
)
from jarvis.agents.diagnostics import (
    AgentDiagnosticsReport,
    collect_agent_diagnostics,
)
from jarvis.agents.manager import AgentManager, AgentRunStore
from jarvis.agents.metrics import AgentMetricsCollector, AgentMetricsSnapshot
from jarvis.agents.recovery import AgentRecoveryService
from jarvis.agents.plan_models import (
    ExecutionPlan,
    PlanBudget,
    PlanCheckpoint,
    PlanResult,
    PlanStep,
    PlanStepStatus,
)
from jarvis.agents.planner import (
    PlanApprovalRequiredError,
    PlanBudgetError,
    PlanValidationError,
    TaskPlanner,
)
from jarvis.agents.schedule_models import (
    MissedPolicy,
    ScheduledJob,
    ScheduledJobStatus,
    ScheduleKind,
)
from jarvis.agents.scheduler import ScheduledJobStore, TaskScheduler
from jarvis.agents.models import (
    TERMINAL_AGENT_STATUSES,
    VALID_AGENT_TRANSITIONS,
    AgentContext,
    AgentErrorCategory,
    AgentResult,
    AgentRunRecord,
    AgentStatus,
)
from jarvis.agents.registry import AgentRegistry

__all__ = [
    "AgentCancelledError",
    "AgentCapacityError",
    "AgentContext",
    "AgentDiagnosticsReport",
    "AgentDuplicateError",
    "AgentError",
    "AgentErrorCategory",
    "AgentManager",
    "AgentMetricsCollector",
    "AgentMetricsSnapshot",
    "AgentNotFoundError",
    "AgentRecoveryService",
    "AgentRegistry",
    "AgentResult",
    "AgentRunRecord",
    "AgentRunStore",
    "AgentStatus",
    "AgentTransitionError",
    "BaseAgent",
    "DelegationCoordinator",
    "DelegationError",
    "DelegationPolicy",
    "ExecutionPlan",
    "MissedPolicy",
    "PlanApprovalRequiredError",
    "PlanBudget",
    "PlanBudgetError",
    "PlanCheckpoint",
    "PlanResult",
    "PlanStep",
    "PlanStepStatus",
    "PlanValidationError",
    "ScheduleKind",
    "ScheduledJob",
    "ScheduledJobStatus",
    "ScheduledJobStore",
    "TERMINAL_AGENT_STATUSES",
    "TaskPlanner",
    "TaskScheduler",
    "VALID_AGENT_TRANSITIONS",
    "collect_agent_diagnostics",
]
