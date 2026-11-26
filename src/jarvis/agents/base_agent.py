"""Base agent contract and error hierarchy (Phase 4.1, zero PySide6)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from jarvis.agents.models import AgentContext, AgentResult
from jarvis.core.exceptions import JarvisError


class AgentError(JarvisError):
    """Base error for the agent runtime."""


class AgentNotFoundError(AgentError):
    """Requested agent ID is not registered."""


class AgentDuplicateError(AgentError):
    """Attempted to register an agent ID that already exists."""


class AgentTransitionError(AgentError):
    """Invalid agent run state transition."""


class AgentCapacityError(AgentError):
    """Agent runtime is shutting down or cannot accept work."""


class AgentCancelledError(AgentError):
    """Agent run was cancelled."""


class BaseAgent(ABC):
    """Abstract base class for all JARVIS agents."""

    agent_id: str
    name: str
    capabilities: frozenset[str] = frozenset()

    @abstractmethod
    async def run(self, context: AgentContext) -> AgentResult:
        """Execute one bounded agent task."""
