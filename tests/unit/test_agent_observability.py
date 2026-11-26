"""Unit tests for Phase 4.6 AgentMetricsCollector and privacy-safe diagnostics."""

from __future__ import annotations

from jarvis.agents import (
    AgentManager,
    AgentMetricsCollector,
    AgentRegistry,
    collect_agent_diagnostics,
)
from jarvis.core.event_bus import EventBus
from tests.unit.test_agents import _EchoAgent, _FailingAgent, _InMemoryRunStore


class _ListableStore(_InMemoryRunStore):
    async def list_runs(self, *, limit: int = 200):
        return list(self.runs.values())[:limit]


async def test_metrics_and_privacy_safe_diagnostics() -> None:
    reg = AgentRegistry()
    reg.register(_EchoAgent())
    reg.register(_FailingAgent())
    store = _ListableStore()
    mgr = AgentManager(reg, store, EventBus())  # type: ignore[arg-type]

    r1 = await mgr.submit("echo", "SUPER_SECRET_USER_PROMPT")
    await mgr.wait(r1)
    r2 = await mgr.submit("boom", "ANOTHER_SECRET_INPUT")
    await mgr.wait(r2)

    collector = AgentMetricsCollector(store, mgr)  # type: ignore[arg-type]
    report = await collect_agent_diagnostics(mgr, collector)

    assert report.healthy is True
    assert report.metrics.total_runs == 2
    assert report.metrics.completed == 1
    assert report.metrics.failed == 1
    assert "echo" in report.registered_agents
    # Privacy check: no raw user prompts or outputs in diagnostic report.
    rendered = repr(report)
    assert "SUPER_SECRET_USER_PROMPT" not in rendered
    assert "ANOTHER_SECRET_INPUT" not in rendered
