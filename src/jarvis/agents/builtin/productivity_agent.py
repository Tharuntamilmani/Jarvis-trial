"""ProductivityAgent: workflow summary and approval-gated scheduling (Phase 4.7)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from jarvis.agents.base_agent import BaseAgent
from jarvis.agents.models import AgentContext, AgentResult, AgentStatus

if TYPE_CHECKING:  # pragma: no cover - typing only
    from jarvis.agents.scheduler import TaskScheduler


class ProductivityAgent(BaseAgent):
    """Summarizes active schedules and workflows without privilege escalation."""

    agent_id = "productivity"
    name = "Productivity Agent"
    capabilities = frozenset({"productivity", "scheduling"})

    def __init__(self, scheduler: TaskScheduler | None = None) -> None:
        self._scheduler = scheduler

    async def run(self, context: AgentContext) -> AgentResult:
        del context
        if self._scheduler is None:
            return AgentResult(
                output="No scheduled tasks configured.",
                status=AgentStatus.COMPLETED,
            )
        jobs = self._scheduler.list_jobs()
        if not jobs:
            return AgentResult(
                output="Scheduled jobs: 0.",
                status=AgentStatus.COMPLETED,
            )
        lines = [f"Scheduled jobs ({len(jobs)}):"]
        for job in jobs:
            lines.append(
                f"- {job.agent_id} ({job.kind.value}, {job.status.value})"
            )
        return AgentResult(
            output="\n".join(lines),
            status=AgentStatus.COMPLETED,
        )
