"""Unit tests for Phase 4.3 built-in safe & read-only agents."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from jarvis.agents.builtin import ChatAgent, ResearchAgent, SystemAgent
from jarvis.agents.models import AgentContext, AgentStatus
from jarvis.core.event_bus import EventBus
from jarvis.core.events import ChatStreamCompleted
from jarvis.core.lifecycle import LifecycleManager
from jarvis.core.service_registry import ServiceRegistry
from jarvis.memory.long_term import MemoryRecord, MemorySearchResult


class _FakeChatService:
    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self.started: list[str] = []
        self.cancelled: list[str] = []
        self.gate: asyncio.Event | None = None

    async def start_chat(self, messages, *, request_id=None) -> str:
        self.started.append(request_id)
        if self.gate is not None:
            return request_id
        await self.bus.publish(
            ChatStreamCompleted(
                request_id=request_id,
                content=f"reply:{messages[-1].content}",
                tokens_used=2,
            )
        )
        return request_id

    async def cancel(self, request_id: str) -> None:
        self.cancelled.append(request_id)


class _FakeMemoryManager:
    def __init__(self, results: list[MemorySearchResult]) -> None:
        self._results = results
        self.queries: list[str] = []

    async def search(self, query: str, *, limit: int = 5):
        self.queries.append(query)
        return self._results[:limit]


async def test_chat_agent_routes_through_chat_service_and_cancels() -> None:
    bus = EventBus()
    chat = _FakeChatService(bus)
    agent = ChatAgent(chat, bus)  # type: ignore[arg-type]

    res = await agent.run(
        AgentContext(run_id="r1", agent_id="chat", input_text="hello")
    )
    assert res.status is AgentStatus.COMPLETED
    assert res.output == "reply:hello"
    assert len(chat.started) == 1

    # Cancellation propagates to ChatService.cancel().
    chat.gate = asyncio.Event()
    task = asyncio.create_task(
        agent.run(AgentContext(run_id="r2", agent_id="chat", input_text="wait"))
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(chat.cancelled) == 1


async def test_research_agent_queries_local_memory_only() -> None:
    rec = MemoryRecord(
        id="m1",
        kind="rag_chunk",
        content="JARVIS uses PostgreSQL 16.",
        importance=0.8,
        confidence=1.0,
        source="rag:abc",
        created_at=datetime.now(timezone.utc),
    )
    mgr = _FakeMemoryManager([MemorySearchResult(record=rec, similarity=0.95, score=0.9)])
    agent = ResearchAgent(mgr)  # type: ignore[arg-type]

    res = await agent.run(
        AgentContext(run_id="r3", agent_id="research", input_text="database")
    )
    assert res.status is AgentStatus.COMPLETED
    assert "PostgreSQL 16" in res.output
    assert res.metadata["sources"] == ["rag:abc"]
    assert mgr.queries == ["database"]


async def test_system_agent_is_read_only() -> None:
    bus = EventBus()
    lifecycle = LifecycleManager(ServiceRegistry(), bus)
    agent = SystemAgent(lifecycle)

    res = await agent.run(
        AgentContext(run_id="r4", agent_id="system", input_text="status")
    )
    assert res.status is AgentStatus.COMPLETED
    assert "Platform:" in res.output
    assert "Python:" in res.output
    assert "Kill switch: armed" in res.output
