"""Unit tests for Phase 4.1 agent contracts, registry, and manager."""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timezone

import pytest

from jarvis.agents import (
    AgentCapacityError,
    AgentContext,
    AgentDuplicateError,
    AgentError,
    AgentErrorCategory,
    AgentManager,
    AgentNotFoundError,
    AgentRegistry,
    AgentResult,
    AgentRunRecord,
    AgentStatus,
    AgentTransitionError,
    BaseAgent,
    VALID_AGENT_TRANSITIONS,
)
from jarvis.core.event_bus import EventBus
from jarvis.core.events import AgentRunFinished, AgentRunStarted
from jarvis.core.exceptions import KillSwitchActiveError
from jarvis.core.lifecycle import LifecycleManager
from jarvis.core.service_registry import ServiceRegistry


class _InMemoryRunStore:
    def __init__(self) -> None:
        self.runs: dict[str, AgentRunRecord] = {}
        self.keys: dict[str, str] = {}

    async def create_run(
        self,
        *,
        run_id: str | None = None,
        agent_id: str,
        input_text: str,
        owner_session_id: str | None = None,
        parent_run_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[AgentRunRecord, bool]:
        if idempotency_key and idempotency_key in self.keys:
            return self.runs[self.keys[idempotency_key]], False
        rid = run_id or uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        rec = AgentRunRecord(
            id=rid,
            agent=agent_id,
            input=input_text,
            output=None,
            status=AgentStatus.QUEUED,
            duration_ms=None,
            idempotency_key=idempotency_key,
            error_category=None,
            owner_session_id=owner_session_id,
            parent_run_id=parent_run_id,
            created_at=now,
            updated_at=now,
        )
        self.runs[rid] = rec
        if idempotency_key:
            self.keys[idempotency_key] = rid
        return rec, True

    async def transition(
        self,
        run_id: str,
        *,
        to_status: AgentStatus,
        output: str | None = None,
        duration_ms: int | None = None,
        error_category: str | None = None,
    ) -> AgentRunRecord:
        cur = self.runs[run_id]
        allowed = VALID_AGENT_TRANSITIONS.get(cur.status, frozenset())
        if to_status not in allowed:
            raise AgentTransitionError(f"invalid {cur.status} -> {to_status}")
        updated = AgentRunRecord(
            id=cur.id,
            agent=cur.agent,
            input=cur.input,
            output=output if output is not None else cur.output,
            status=to_status,
            duration_ms=duration_ms if duration_ms is not None else cur.duration_ms,
            idempotency_key=cur.idempotency_key,
            error_category=error_category
            if error_category is not None
            else cur.error_category,
            owner_session_id=cur.owner_session_id,
            parent_run_id=cur.parent_run_id,
            created_at=cur.created_at,
            updated_at=datetime.now(timezone.utc),
        )
        self.runs[run_id] = updated
        return updated

    async def get_run(self, run_id: str) -> AgentRunRecord | None:
        return self.runs.get(run_id)


class _EchoAgent(BaseAgent):
    agent_id = "echo"
    name = "Echo"
    capabilities = frozenset({"echo"})

    async def run(self, context: AgentContext) -> AgentResult:
        return AgentResult(output=f"echo:{context.input_text}")


class _GatedAgent(BaseAgent):
    agent_id = "gated"
    name = "Gated"
    capabilities = frozenset({"slow"})

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.active = 0
        self.peak = 0

    async def run(self, context: AgentContext) -> AgentResult:
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.entered.set()
        try:
            await self.release.wait()
            return AgentResult(output="done")
        finally:
            self.active -= 1


class _FailingAgent(BaseAgent):
    agent_id = "boom"
    name = "Boom"

    async def run(self, context: AgentContext) -> AgentResult:
        raise RuntimeError("secret internal failure")


def test_agent_package_never_imports_pyside6() -> None:
    assert not any(
        name.startswith("jarvis.agents") and "PySide6" in name
        for name in sys.modules
    )


def test_registry_registration_duplicates_and_capabilities() -> None:
    reg = AgentRegistry()
    echo = _EchoAgent()
    reg.register(echo)
    assert reg.has("echo") is True
    assert reg.get("echo") is echo
    assert reg.find_by_capability("echo") == [echo]

    with pytest.raises(AgentDuplicateError):
        reg.register(_EchoAgent())
    with pytest.raises(AgentNotFoundError):
        reg.get("missing")
    with pytest.raises(AgentError):
        reg.register("not-an-agent")  # type: ignore[arg-type]


async def test_manager_executes_emits_events_and_enforces_idempotency() -> None:
    reg = AgentRegistry()
    reg.register(_EchoAgent())
    store = _InMemoryRunStore()
    bus = EventBus()
    events: list[object] = []
    bus.subscribe(AgentRunStarted, events.append)
    bus.subscribe(AgentRunFinished, events.append)

    mgr = AgentManager(reg, store, bus, max_concurrent=2)  # type: ignore[arg-type]
    r1 = await mgr.submit("echo", "hi", idempotency_key="k1")
    r2 = await mgr.submit("echo", "hi", idempotency_key="k1")
    assert r1 == r2

    rec = await mgr.wait(r1)
    assert rec.status is AgentStatus.COMPLETED
    assert rec.output == "echo:hi"
    assert any(isinstance(e, AgentRunStarted) for e in events)
    assert any(isinstance(e, AgentRunFinished) for e in events)


async def test_manager_concurrency_bounds_and_independent_cancellation() -> None:
    reg = AgentRegistry()
    gated = _GatedAgent()
    reg.register(gated)
    reg.register(_EchoAgent())
    store = _InMemoryRunStore()
    bus = EventBus()

    mgr = AgentManager(reg, store, bus, max_concurrent=1)  # type: ignore[arg-type]
    r_slow = await mgr.submit("gated", "one")
    await gated.entered.wait()
    r_echo = await mgr.submit("echo", "two")
    # Echo is queued behind gated because max_concurrent == 1.
    assert gated.peak == 1

    assert await mgr.cancel(r_slow) is True
    rec_slow = await mgr.wait(r_slow)
    assert rec_slow.status is AgentStatus.CANCELLED
    assert rec_slow.error_category == AgentErrorCategory.CANCELLED.value

    rec_echo = await mgr.wait(r_echo)
    assert rec_echo.status is AgentStatus.COMPLETED
    assert rec_echo.output == "echo:two"


async def test_manager_timeout_and_failure_isolation() -> None:
    reg = AgentRegistry()
    gated = _GatedAgent()
    reg.register(gated)
    reg.register(_FailingAgent())
    store = _InMemoryRunStore()
    mgr = AgentManager(reg, store, EventBus(), default_timeout_seconds=0.05)  # type: ignore[arg-type]

    r_to = await mgr.submit("gated", "slow")
    rec_to = await mgr.wait(r_to)
    assert rec_to.status is AgentStatus.TIMED_OUT
    assert rec_to.error_category == AgentErrorCategory.TIMEOUT.value

    r_fail = await mgr.submit("boom", "x")
    rec_fail = await mgr.wait(r_fail)
    assert rec_fail.status is AgentStatus.FAILED
    assert rec_fail.error_category == AgentErrorCategory.EXECUTION_ERROR.value
    assert "secret" not in (rec_fail.output or "")


async def test_manager_kill_switch_and_shutdown_drainage() -> None:
    bus = EventBus()
    lifecycle = LifecycleManager(ServiceRegistry(), bus)
    reg = AgentRegistry()
    gated = _GatedAgent()
    reg.register(gated)
    store = _InMemoryRunStore()
    mgr = AgentManager(reg, store, bus, lifecycle=lifecycle)  # type: ignore[arg-type]

    rid = await mgr.submit("gated", "work")
    await gated.entered.wait()
    await lifecycle.trigger_kill_switch("test")
    rec = await mgr.wait(rid)
    assert rec.status is AgentStatus.CANCELLED
    assert rec.error_category == AgentErrorCategory.KILL_SWITCH.value

    with pytest.raises(KillSwitchActiveError):
        await mgr.submit("gated", "blocked")

    await lifecycle.rearm_kill_switch()
    await mgr.close()
    with pytest.raises(AgentCapacityError):
        await mgr.submit("gated", "after close")
