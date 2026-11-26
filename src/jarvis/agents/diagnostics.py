"""Privacy-safe agent runtime diagnostics (Phase 4.6)."""

from __future__ import annotations

from dataclasses import dataclass

from jarvis.agents.manager import AgentManager
from jarvis.agents.metrics import AgentMetricsCollector, AgentMetricsSnapshot
from jarvis.agents.scheduler import TaskScheduler


@dataclass(frozen=True)
class AgentDiagnosticsReport:
    """Secret-free, content-free agent runtime health report."""

    registered_agents: tuple[str, ...]
    active_run_count: int
    scheduled_job_count: int
    metrics: AgentMetricsSnapshot
    healthy: bool


async def collect_agent_diagnostics(
    manager: AgentManager,
    metrics: AgentMetricsCollector,
    scheduler: TaskScheduler | None = None,
) -> AgentDiagnosticsReport:
    """Build a privacy-safe diagnostic summary (no inputs, outputs, or secrets)."""
    snap = await metrics.snapshot()
    job_count = len(scheduler.list_jobs()) if scheduler is not None else 0
    return AgentDiagnosticsReport(
        registered_agents=manager.registry.list_ids(),
        active_run_count=len(manager.active_run_ids),
        scheduled_job_count=job_count,
        metrics=snap,
        healthy=True,
    )
