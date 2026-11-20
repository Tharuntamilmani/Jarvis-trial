"""Lifecycle manager tests, including the kill switch."""

from __future__ import annotations

import asyncio

import pytest

from jarvis.core.event_bus import EventBus
from jarvis.core.events import (
    KillSwitchRearmed,
    KillSwitchTriggered,
    ServiceStatusChanged,
    ShutdownCompleted,
    StartupCompleted,
)
from jarvis.core.lifecycle import LifecycleManager, ServiceStatus
from jarvis.core.service_registry import ServiceRegistry


@pytest.mark.asyncio
async def test_ordered_startup_and_health() -> None:
    registry = ServiceRegistry()
    bus = EventBus()
    lifecycle = LifecycleManager(registry, bus)

    registry.register("first", lambda: {"n": 1})
    registry.register("second", lambda: {"n": 2})
    lifecycle.set_health_check("first", lambda: True)
    lifecycle.set_health_check("second", lambda: False)  # degraded service

    started: list[bool] = []
    bus.subscribe(StartupCompleted, lambda e: started.append(True))

    await lifecycle.startup()
    statuses = lifecycle.statuses
    assert statuses["first"] is ServiceStatus.HEALTHY
    assert statuses["second"] is ServiceStatus.UNAVAILABLE
    assert started == [True]


@pytest.mark.asyncio
async def test_failing_factory_degrades_startup() -> None:
    registry = ServiceRegistry()
    bus = EventBus()
    lifecycle = LifecycleManager(registry, bus)

    def broken() -> int:
        raise ValueError("no")

    registry.register("broken", broken)
    registry.register("after", lambda: "ok")
    await lifecycle.startup()
    assert registry.is_resolved("after")
    assert lifecycle.statuses["broken"] is ServiceStatus.UNAVAILABLE


@pytest.mark.asyncio
async def test_shutdown_reverse_order() -> None:
    registry = ServiceRegistry()
    bus = EventBus()
    lifecycle = LifecycleManager(registry, bus)
    order: list[str] = []

    def make_clean(name: str):
        def cleanup(instance) -> None:
            order.append(name)
        return cleanup

    registry.register("a", lambda: 1, cleanup=make_clean("a"))
    registry.register("b", lambda: 2, cleanup=make_clean("b"))
    await lifecycle.startup()
    done: list[str] = []
    bus.subscribe(ShutdownCompleted, lambda e: done.append("shutdown"))
    await lifecycle.shutdown()
    assert order == ["b", "a"]
    assert done == ["shutdown"]
    assert lifecycle.statuses["a"] is ServiceStatus.STOPPED


@pytest.mark.asyncio
async def test_kill_switch_cancels_tasks_and_blocks() -> None:
    from jarvis.core.exceptions import KillSwitchActiveError

    registry = ServiceRegistry()
    bus = EventBus()
    lifecycle = LifecycleManager(registry, bus)

    kill_events: list[str] = []
    bus.subscribe(KillSwitchTriggered, lambda e: kill_events.append(e.reason))
    bus.subscribe(KillSwitchRearmed, lambda e: kill_events.append("rearmed"))

    async def forever() -> None:
        await asyncio.sleep(100)

    task = asyncio.ensure_future(forever())
    lifecycle.register_killable_task(task)

    await lifecycle.trigger_kill_switch(reason="test")
    assert lifecycle.kill_switch_active
    assert kill_events == ["test"]
    await asyncio.sleep(0)
    assert task.cancelled()

    with pytest.raises(KillSwitchActiveError):
        async with lifecycle.guard_sensitive():
            pass

    await lifecycle.rearm_kill_switch()
    assert kill_events == ["test", "rearmed"]
    assert not lifecycle.kill_switch_active


@pytest.mark.asyncio
async def test_status_events_published() -> None:
    registry = ServiceRegistry()
    bus = EventBus()
    lifecycle = LifecycleManager(registry, bus)
    events: list[ServiceStatusChanged] = []
    bus.subscribe(ServiceStatusChanged, events.append)

    registry.register("svc", lambda: 1)
    lifecycle.set_health_check("svc", lambda: True)
    await lifecycle.startup()
    assert any(e.service == "svc" and e.status == "healthy" for e in events)
