"""ResearchAgent: strictly local memory + RAG synthesis (never sent to Groq)."""

from __future__ import annotations

from jarvis.agents.base_agent import BaseAgent
from jarvis.agents.models import AgentContext, AgentResult, AgentStatus
from jarvis.memory.manager import MemoryManager


class ResearchAgent(BaseAgent):
    """Queries local pgvector memories and RAG chunks; never calls cloud APIs."""

    agent_id = "research"
    name = "Research Agent"
    capabilities = frozenset({"research", "memory"})

    def __init__(self, memory: MemoryManager) -> None:
        self._memory = memory

    async def run(self, context: AgentContext) -> AgentResult:
        query = context.input_text.strip()
        if not query:
            return AgentResult(output="", status=AgentStatus.COMPLETED)

        results = await self._memory.search(query, limit=5)
        if not results:
            return AgentResult(
                output="No matching local memories or documents found.",
                status=AgentStatus.COMPLETED,
                metadata={"sources": []},
            )

        lines: list[str] = ["Local research findings:"]
        sources: list[str] = []
        for idx, hit in enumerate(results, start=1):
            rec = hit.record
            sources.append(rec.source)
            lines.append(
                f"{idx}. [{rec.kind} · {hit.similarity:.2f}] {rec.content}"
            )
        return AgentResult(
            output="\n".join(lines),
            status=AgentStatus.COMPLETED,
            metadata={"sources": sources},
        )
