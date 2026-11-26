"""CodingAgent: read-only project inspection and change proposals (Phase 4.7)."""

from __future__ import annotations

from pathlib import Path

from jarvis.agents.base_agent import BaseAgent
from jarvis.agents.models import AgentContext, AgentResult, AgentStatus


class CodingAgent(BaseAgent):
    """Read-only code analysis and test recommendations.

    Actual file modifications are strictly deferred to Phase 7 approved tools.
    """

    agent_id = "coding"
    name = "Coding Agent"
    capabilities = frozenset({"code_analysis", "read_only"})

    def __init__(self, workspace_root: Path | None = None) -> None:
        self._root = workspace_root

    async def run(self, context: AgentContext) -> AgentResult:
        target = context.metadata.get("path")
        if target and self._root is not None:
            resolved = (self._root / str(target)).resolve()
            root_resolved = self._root.resolve()
            if not resolved.is_relative_to(root_resolved):
                return AgentResult(
                    output="Rejected path outside workspace root.",
                    status=AgentStatus.FAILED,
                    error_category="validation_error",
                )
            if resolved.is_dir():
                entries = sorted(p.name for p in resolved.iterdir())[:50]
                return AgentResult(
                    output="Directory listing:\n" + "\n".join(entries),
                    status=AgentStatus.COMPLETED,
                    metadata={"read_only": True},
                )
            if resolved.is_file():
                lines = resolved.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
                return AgentResult(
                    output=(
                        f"Analyzed {resolved.name} ({len(lines)} lines). "
                        "Read-only proposal mode; edits require Phase 7 tools."
                    ),
                    status=AgentStatus.COMPLETED,
                    metadata={"read_only": True, "line_count": len(lines)},
                )
        return AgentResult(
            output=(
                f"Read-only coding analysis for: {context.input_text.strip()}. "
                "Recommended: run targeted unit tests before applying changes."
            ),
            status=AgentStatus.COMPLETED,
            metadata={"read_only": True},
        )
