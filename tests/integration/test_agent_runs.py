"""PostgreSQL integration tests for Phase 4.1 AgentRunStore & migration 0003."""

from __future__ import annotations

import pytest

from jarvis.agents import (
    AgentContext,
    AgentErrorCategory,
    AgentManager,
    AgentRegistry,
    AgentResult,
    AgentRunStore,
    AgentStatus,
    AgentTransitionError,
    BaseAgent,
)
from jarvis.core.event_bus import EventBus


class _EchoAgent(BaseAgent):
    agent_id = "echo"
    name = "Echo"

    async def run(self, context: AgentContext) -> AgentResult:
        return AgentResult(output=f"ok:{context.input_text}")


async def test_agent_run_store_persists_and_enforces_transitions(
    db_service, clean_database
) -> None:
    store = AgentRunStore(db_service)
    rec, created = await store.create_run(
        agent_id="echo",
        input_text="hello",
        owner_session_id="a" * 32,
        idempotency_key="idem-1",
    )
    assert created is True
    assert rec.status is AgentStatus.QUEUED

    # Idempotency key returns the same run without duplicating.
    dup, created_dup = await store.create_run(
        agent_id="echo",
        input_text="hello",
        idempotency_key="idem-1",
    )
    assert created_dup is False
    assert dup.id == rec.id

    running = await store.transition(rec.id, to_status=AgentStatus.RUNNING)
    assert running.status is AgentStatus.RUNNING

    done = await store.transition(
        rec.id,
        to_status=AgentStatus.COMPLETED,
        output="ok:hello",
        duration_ms=12,
    )
    assert done.status is AgentStatus.COMPLETED
    assert done.output == "ok:hello"

    # Terminal state cannot transition back to running.
    with pytest.raises(AgentTransitionError):
        await store.transition(rec.id, to_status=AgentStatus.RUNNING)


async def test_agent_manager_persists_run_to_postgres(
    db_service, clean_database
) -> None:
    reg = AgentRegistry()
    reg.register(_EchoAgent())
    mgr = AgentManager.from_database(db_service, EventBus(), registry=reg)

    rid = await mgr.submit("echo", "pg-test")
    rec = await mgr.wait(rid)
    assert rec.status is AgentStatus.COMPLETED
    assert rec.output == "ok:pg-test"

    listed = await mgr._store.list_runs(limit=10)  # noqa: SLF001
    assert len(listed) == 1
    assert listed[0].id == rid
    assert listed[0].error_category is None or listed[0].error_category == AgentErrorCategory.CANCELLED.value


async def test_scheduler_persists_jobs_across_restart(
    db_service, clean_database
) -> None:
    from datetime import datetime, timezone
    from jarvis.agents import ScheduleKind, ScheduledJob, ScheduledJobStore, TaskScheduler

    reg = AgentRegistry()
    reg.register(_EchoAgent())
    mgr = AgentManager.from_database(db_service, EventBus(), registry=reg)
    store1 = ScheduledJobStore(db_service)
    sched1 = TaskScheduler(mgr, store1)

    t0 = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
    job = ScheduledJob.create(
        agent_id="echo",
        input_text="persisted",
        kind=ScheduleKind.ONCE,
        next_run_at=t0,
    )
    await sched1.add_job(job)

    # Simulate restart with fresh store & scheduler.
    sched2 = TaskScheduler(mgr, ScheduledJobStore(db_service))
    await sched2.load()
    assert [j.job_id for j in sched2.list_jobs()] == [job.job_id]
    runs = await sched2.tick(now=t0)
    assert len(runs) == 1
    rec = await mgr.wait(runs[0])
    assert rec.output == "ok:persisted"


async def test_recovery_service_demotes_stale_runs_and_correlates_children(
    db_service, clean_database
) -> None:
    from jarvis.agents import AgentRecoveryService

    store = AgentRunStore(db_service)
    stale_q, _ = await store.create_run(
        agent_id="echo",
        input_text="crashed-queued",
        owner_session_id="b" * 32,
    )
    stale_r, _ = await store.create_run(
        agent_id="echo",
        input_text="crashed-running",
        owner_session_id="b" * 32,
    )
    await store.transition(stale_r.id, to_status=AgentStatus.RUNNING)

    live_r, _ = await store.create_run(
        agent_id="echo",
        input_text="live",
        owner_session_id="a" * 32,
    )
    child, _ = await store.create_run(
        agent_id="echo",
        input_text="child",
        owner_session_id="a" * 32,
        parent_run_id=live_r.id,
    )

    recovery = AgentRecoveryService(db_service)
    demoted = await recovery.reconcile_stale_runs(current_session_id="a" * 32)
    assert demoted == 2

    q_after = await store.get_run(stale_q.id)
    r_after = await store.get_run(stale_r.id)
    live_after = await store.get_run(live_r.id)
    assert q_after is not None and q_after.status is AgentStatus.INTERRUPTED
    assert r_after is not None and r_after.status is AgentStatus.INTERRUPTED
    assert live_after is not None and live_after.status is AgentStatus.QUEUED

    children = await recovery.find_children(live_r.id)
    assert [c.id for c in children] == [child.id]
