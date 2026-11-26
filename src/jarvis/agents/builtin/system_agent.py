"""SystemAgent: strictly read-only service and runtime health summary."""

from __future__ import annotations

import platform
import sys
from typing import TYPE_CHECKING

from jarvis.agents.base_agent import BaseAgent
from jarvis.agents.models import AgentContext, AgentResult, AgentStatus

if TYPE_CHECKING:  # pragma: no cover - typing only
    from jarvis.core.lifecycle import LifecycleManager


class SystemAgent(BaseAgent):
    """Reports read-only system and service status; never executes commands."""

    agent_id = "system"
    name = "System Agent"
    capabilities = frozenset({"system_readonly"})

    def __init__(self, lifecycle: LifecycleManager | None = None) -> None:
        self._lifecycle = lifecycle

    async def run(self, context: AgentContext) -> AgentResult:
        del context
        py_ver = (
            f"{sys.version_info.major}."
            f"{sys.version_info.minor}."
            f"{sys.version_info.micro}"
        )
        lines = [
            f"Platform: {platform.system()} {platform.release()}",
            f"Python: {py_ver}",
        ]
        if self._lifecycle is not None:
            lines.append(
                f"Kill switch: {'ENGAGED' if self._lifecycle.kill_switch_active else 'armed'}"
            )
            statuses = self._lifecycle.statuses
            if statuses:
                svc_summary = ", ".join(
                    f"{k}={v.value}" for k, v in sorted(statuses.items())
                )
                lines.append(f"Services: {svc_summary}")
        return AgentResult(
            output="\n".join(lines),
            status=AgentStatus.COMPLETED,
        )
