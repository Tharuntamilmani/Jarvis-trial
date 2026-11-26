"""Agent capability discovery and bounded delegation (Phase 4.7)."""

from __future__ import annotations

from dataclasses import dataclass

from jarvis.agents.base_agent import AgentError, BaseAgent
from jarvis.agents.manager import AgentManager
from jarvis.agents.models import AgentRunRecord


class DelegationError(AgentError):
    """Raised when delegation violates depth, cycle, or capability rules."""


@dataclass(frozen=True)
class DelegationPolicy:
    """Bounds agent-to-agent delegation depth and prevents cycles."""

    max_depth: int = 3


class DelegationCoordinator:
    """Mediates parent-child agent delegation strictly through ``AgentManager``."""

    def __init__(
        self,
        manager: AgentManager,
        policy: DelegationPolicy | None = None,
    ) -> None:
        self._manager = manager
        self._policy = policy or DelegationPolicy()

    def discover(self, capability: str) -> list[BaseAgent]:
        return self._manager.registry.find_by_capability(capability)

    async def delegate(
        self,
        *,
        from_agent_id: str,
        to_agent_id: str,
        input_text: str,
        parent_run_id: str,
        call_chain: tuple[str, ...] = (),
        depth: int = 0,
    ) -> AgentRunRecord:
        if depth >= self._policy.max_depth:
            raise DelegationError(
                f"delegation depth {depth} exceeds max_depth "
                f"{self._policy.max_depth}"
            )
        chain = call_chain or (from_agent_id,)
        if to_agent_id in chain:
            raise DelegationError(
                f"circular delegation rejected: {chain} -> {to_agent_id}"
            )
        if not self._manager.registry.has(to_agent_id):
            raise DelegationError(f"target agent not registered: {to_agent_id!r}")

        child_id = await self._manager.submit(
            to_agent_id,
            input_text,
            parent_run_id=parent_run_id,
            metadata={
                "delegation_depth": depth + 1,
                "call_chain": chain + (to_agent_id,),
            },
        )
        return await self._manager.wait(child_id)
