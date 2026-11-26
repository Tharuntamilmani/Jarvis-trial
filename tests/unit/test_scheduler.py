"""Unit tests for Phase 4.4 persistent TaskScheduler."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from jarvis.agents import (
    AgentManager,
    AgentRegistry,
    MissedPolicy,
    ScheduleKind,
    ScheduledJob,
    ScheduledJobStatus,
    ScheduledJobStore,
    TaskScheduler,
)
from jarvis.core.event_bus import EventBus
from jarvis.core.lifecycle import LifecycleManager
from jarvis.core.service_registry import ServiceRegistry
from tests.unit.test_agents import _EchoAgent, _InMemoryRunStore


def _setup(lifecycle: LifecycleManager | None = None):
    reg = AgentRegistry()
    reg.register(_EchoAgent())
    bus = EventBus()
    mgr = AgentManager(reg, _InMemoryRunStore(), bus, lifecycle=lifecycle)  # type: ignore[arg-type]
    store = ScheduledJobStore()
    sched = TaskScheduler(mgr, store, lifecycle=lifecycle)
    return mgr, store, sched


async def test_one_shot_and_interval_scheduling_with_pause_resume_cancel() -> None:
    mgr, _store, sched = _setup()
    t0 = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)

    once = ScheduledJob.create(
        agent_id="echo",
        input_text="once",
        kind=ScheduleKind.ONCE,
        next_run_at=t0,
    )
    interval = ScheduledJob.create(
        agent_id="echo",
        input_text="tick",
        kind=ScheduleKind.INTERVAL,
        next_run_at=t0,
        interval_seconds=60.0,
        missed_policy=MissedPolicy.RUN_ONCE,
    )
    await sched.add_job(once)
    await sched.add_job(interval)

    runs = await sched.tick(now=t0)
    assert len(runs) == 2
    for rid in runs:
        rec = await mgr.wait(rid)
        assert rec.output in ("echo:once", "echo:tick")

    # Duplicate tick at same instant must NOT re-trigger.
    assert await sched.tick(now=t0) == []

    # Pause blocks next interval trigger; resume restores it.
    await sched.pause_job(interval.job_id)
    assert await sched.tick(now=t0 + timedelta(seconds=61)) == []
    await sched.resume_job(interval.job_id)
    runs2 = await sched.tick(now=t0 + timedelta(seconds=61))
    assert len(runs2) == 1

    # Cancel stops it permanently.
    await sched.cancel_job(interval.job_id)
    assert await sched.tick(now=t0 + timedelta(seconds=180)) == []


async def test_missed_policies_kill_switch_and_shutdown() -> None:
    bus = EventBus()
    lifecycle = LifecycleManager(ServiceRegistry(), bus)
    _mgr, _store, sched = _setup(lifecycle=lifecycle)
    t0 = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)

    skip_job = ScheduledJob.create(
        agent_id="echo",
        input_text="skip",
        kind=ScheduleKind.INTERVAL,
        next_run_at=t0,
        interval_seconds=10.0,
        missed_policy=MissedPolicy.SKIP,
    )
    approval_job = ScheduledJob.create(
        agent_id="echo",
        input_text="danger",
        kind=ScheduleKind.ONCE,
        next_run_at=t0,
        missed_policy=MissedPolicy.REQUIRE_APPROVAL,
    )
    await sched.add_job(skip_job)
    await sched.add_job(approval_job)

    # Kill switch blocks all triggers.
    await lifecycle.trigger_kill_switch("test")
    assert await sched.tick(now=t0 + timedelta(seconds=120)) == []
    await lifecycle.rearm_kill_switch()

    # Missed skip advances without executing; require_approval awaits approval.
    assert await sched.tick(now=t0 + timedelta(seconds=120)) == []
    jobs = {j.job_id: j for j in sched.list_jobs()}
    assert jobs[skip_job.job_id].next_run_at > t0 + timedelta(seconds=120)
    assert (
        jobs[approval_job.job_id].status
        is ScheduledJobStatus.AWAITING_APPROVAL
    )

    sched.start()
    await sched.close()
