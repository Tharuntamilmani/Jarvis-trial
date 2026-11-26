"""Agent registry for discovering and validating agents (Phase 4.1)."""

from __future__ import annotations

from jarvis.agents.base_agent import (
    AgentDuplicateError,
    AgentError,
    AgentNotFoundError,
    BaseAgent,
)


class AgentRegistry:
    """In-process registry of validated ``BaseAgent`` instances."""

    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        if not isinstance(agent, BaseAgent):
            raise AgentError("agent must inherit from BaseAgent")
        agent_id = getattr(agent, "agent_id", "").strip()
        if not agent_id:
            raise AgentError("agent_id cannot be empty")
        if agent_id in self._agents:
            raise AgentDuplicateError(f"agent already registered: {agent_id!r}")
        self._agents[agent_id] = agent

    def get(self, agent_id: str) -> BaseAgent:
        agent = self._agents.get(agent_id)
        if agent is None:
            raise AgentNotFoundError(f"agent not registered: {agent_id!r}")
        return agent

    def has(self, agent_id: str) -> bool:
        return agent_id in self._agents

    def list_ids(self) -> tuple[str, ...]:
        return tuple(self._agents.keys())

    def find_by_capability(self, capability: str) -> list[BaseAgent]:
        return [
            agent
            for agent in self._agents.values()
            if capability in getattr(agent, "capabilities", ())
        ]
