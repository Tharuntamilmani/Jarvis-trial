"""Event bus tests."""

from __future__ import annotations

import logging

import pytest

from jarvis.core.event_bus import EventBus
from jarvis.core.events import ServiceStatusChanged


@pytest.mark.asyncio
async def test_publish_reaches_subscribers() -> None:
    bus = EventBus()
    received: list[ServiceStatusChanged] = []

    async def handler(event: ServiceStatusChanged) -> None:
        received.append(event)

    def sync_handler(event: ServiceStatusChanged) -> None:
        received.append(event)

    bus.subscribe(ServiceStatusChanged, handler)
    bus.subscribe("service_status_changed", sync_handler)
    await bus.publish(ServiceStatusChanged(service="db", status="healthy"))
    assert len(received) == 2
    assert received[0].service == "db"


@pytest.mark.asyncio
async def test_failing_handler_is_isolated(caplog) -> None:
    bus = EventBus()
    ok: list[str] = []

    async def bad(_event) -> None:
        raise RuntimeError("boom")

    def good(_event) -> None:
        ok.append("done")

    bus.subscribe(ServiceStatusChanged, bad)
    bus.subscribe(ServiceStatusChanged, good)
    with caplog.at_level(logging.ERROR):
        await bus.publish(ServiceStatusChanged(service="db", status="healthy"))
    assert ok == ["done"]
    assert any("boom" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_unsubscribe() -> None:
    bus = EventBus()
    seen: list[object] = []
    handler = lambda e: seen.append(e)  # noqa: E731
    bus.subscribe(ServiceStatusChanged, handler)
    bus.unsubscribe(ServiceStatusChanged, handler)
    await bus.publish(ServiceStatusChanged(service="db", status="healthy"))
    assert not seen
