"""Agent execution metrics (Phase 4.6)."""

from __future__ import annotations

from dataclasses import dataclass

from jarvis.agents.manager import AgentManager, AgentRunStore
from jarvis.agents.models import AgentStatus


@dataclass(frozen=True)
class AgentMetricsSnapshot:
    """Aggregated agent runtime metrics."""

    total_runs: int
    completed: int
    failed: int
    cancelled: int
    timed_out: int
    interrupted: int
    active_runs: int
    avg_duration_ms: float


class AgentMetricsCollector:
    """Computes runtime metrics from persisted runs and active tasks."""

    def __init__(self, store: AgentRunStore, manager: AgentManager) -> None:
        self._store = store
        self._manager = manager

    async def snapshot(self, *, limit: int = 200) -> AgentMetricsSnapshot:
        runs = await self._store.list_runs(limit=limit)
        completed = sum(1 for r in runs if r.status is AgentStatus.COMPLETED)
        failed = sum(1 for r in runs if r.status is AgentStatus.FAILED)
        cancelled = sum(1 for r in runs if r.status is AgentStatus.CANCELLED)
        timed_out = sum(1 for r in runs if r.status is AgentStatus.TIMED_OUT)
        interrupted = sum(
            1 for r in runs if r.status is AgentStatus.INTERRUPTED
        )
        durations = [r.duration_ms for r in runs if r.duration_ms is not None]
        avg_ms = (
            sum(durations) / len(durations) if durations else 0.0
        )
        return AgentMetricsSnapshot(
            total_runs=len(runs),
            completed=completed,
            failed=failed,
            cancelled=cancelled,
            timed_out=timed_out,
            interrupted=interrupted,
            active_runs=len(self._manager.active_run_ids),
            avg_duration_ms=round(avg_ms, 2),
        )
