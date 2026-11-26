"""Unit tests for Phase 4.7 capability discovery, bounded delegation, and agents."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agents import (
    AgentContext,
    AgentManager,
    AgentRegistry,
    AgentStatus,
    DelegationCoordinator,
    DelegationError,
    DelegationPolicy,
)
from jarvis.agents.builtin import CodingAgent, ProductivityAgent
from jarvis.core.event_bus import EventBus
from tests.unit.test_agents import _EchoAgent, _InMemoryRunStore


async def test_delegation_enforces_depth_and_rejects_cycles() -> None:
    reg = AgentRegistry()
    reg.register(_EchoAgent())
    reg.register(CodingAgent())
    store = _InMemoryRunStore()
    mgr = AgentManager(reg, store, EventBus())  # type: ignore[arg-type]

    coord = DelegationCoordinator(mgr, DelegationPolicy(max_depth=2))
    assert len(coord.discover("code_analysis")) == 1

    rec = await coord.delegate(
        from_agent_id="coding",
        to_agent_id="echo",
        input_text="subtask",
        parent_run_id="1" * 32,
        depth=0,
    )
    assert rec.status is AgentStatus.COMPLETED
    assert rec.output == "echo:subtask"
    assert rec.parent_run_id == "1" * 32

    with pytest.raises(DelegationError, match="max_depth"):
        await coord.delegate(
            from_agent_id="coding",
            to_agent_id="echo",
            input_text="too deep",
            parent_run_id="1" * 32,
            depth=2,
        )

    with pytest.raises(DelegationError, match="circular"):
        await coord.delegate(
            from_agent_id="coding",
            to_agent_id="echo",
            input_text="loop",
            parent_run_id="1" * 32,
            call_chain=("coding", "echo"),
            depth=1,
        )


async def test_coding_agent_is_read_only_and_rejects_traversal(
    tmp_path: Path,
) -> None:
    sample = tmp_path / "hello.py"
    sample.write_text("print('hi')\n", encoding="utf-8")

    agent = CodingAgent(workspace_root=tmp_path)
    ok = await agent.run(
        AgentContext(
            run_id="r1",
            agent_id="coding",
            input_text="inspect",
            metadata={"path": "hello.py"},
        )
    )
    assert ok.status is AgentStatus.COMPLETED
    assert ok.metadata["read_only"] is True
    assert "1 lines" in ok.output

    bad = await agent.run(
        AgentContext(
            run_id="r2",
            agent_id="coding",
            input_text="escape",
            metadata={"path": "../outside.txt"},
        )
    )
    assert bad.status is AgentStatus.FAILED
    assert bad.error_category == "validation_error"


async def test_productivity_agent_summarizes_without_escalation() -> None:
    agent = ProductivityAgent(scheduler=None)
    res = await agent.run(
        AgentContext(run_id="r3", agent_id="productivity", input_text="summary")
    )
    assert res.status is AgentStatus.COMPLETED
    assert "No scheduled tasks" in res.output
