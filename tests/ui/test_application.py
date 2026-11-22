"""DesktopApplication wiring tests (Stage 2.7 integration).

These use a fake core so startup/shutdown and tray actions are tested
without a database, provider network calls, or the real engine.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from jarvis.core.event_bus import EventBus
from jarvis.desktop.application import DesktopApplication


class _FakeProviders:
    def __init__(self) -> None:
        self.refresh_calls: list[str | None] = []

    async def refresh_health(self, provider_id: str | None = None):
        self.refresh_calls.append(provider_id)
        return {}


class _FakeRegistry:
    def __init__(self, values: dict[str, object]) -> None:
        self._values = values

    async def get(self, name: str):
        if name not in self._values:
            raise KeyError(name)
        return self._values[name]


class _FakeEngine:
    def __init__(self) -> None:
        self.bus = EventBus()
        self.providers = _FakeProviders()
        self.registry = _FakeRegistry(
            {
                "providers": self.providers,
                "chat": None,
                "consent": None,
            }
        )
        self.kill_switch_active = False
        self.trigger_calls: list[str] = []
        self.rearm_calls: list[bool] = []
        self.status_refresh_calls = 0

    async def trigger_kill_switch(self, reason: str = "") -> None:
        self.kill_switch_active = True
        self.trigger_calls.append(reason)

    async def rearm_kill_switch(self) -> None:
        self.kill_switch_active = False
        self.rearm_calls.append(True)

    def provider_status_snapshot(self):
        return {}

    async def refresh_provider_statuses(self):
        self.status_refresh_calls += 1
        return {}


class _FakeCore:
    def __init__(self) -> None:
        self.engine = _FakeEngine()
        self.start_calls = 0
        self.shutdown_calls = 0

    async def start(self) -> None:
        self.start_calls += 1

    async def shutdown(self) -> None:
        self.shutdown_calls += 1


@pytest.fixture()
def desktop(qapp) -> DesktopApplication:
    app = DesktopApplication(_FakeCore(), qapp)
    yield app
    app.windows.shutdown()
    app.tray.hide()


async def test_startup_performs_single_provider_refresh(desktop) -> None:
    await desktop.run()
    assert desktop.chat is not None
    await desktop.chat.wait_provider_refresh()

    # The old global refresh_health() call is gone; only the first
    # Workspace show performs one real refresh.
    assert desktop.core.engine.providers.refresh_calls == []
    assert desktop.core.engine.status_refresh_calls == 1


async def test_tray_kill_switch_triggers_and_rearms(desktop) -> None:
    engine = desktop.core.engine

    desktop.tray.kill_switch_requested.emit()
    for _ in range(50):
        if engine.trigger_calls:
            break
        await asyncio.sleep(0)

    assert engine.trigger_calls == ["manual: tray"]
    assert engine.kill_switch_active is True

    desktop.tray.kill_switch_requested.emit()
    for _ in range(50):
        if engine.rearm_calls:
            break
        await asyncio.sleep(0)

    assert engine.rearm_calls == [True]
    assert engine.kill_switch_active is False


async def test_scheduled_task_exception_is_logged(
    desktop, caplog
) -> None:
    async def boom() -> None:
        raise RuntimeError("probe failure")

    with caplog.at_level(
        logging.ERROR, logger="jarvis.desktop.application"
    ):
        desktop._schedule(boom())  # noqa: SLF001
        for _ in range(50):
            if not desktop._tasks:  # noqa: SLF001
                break
            await asyncio.sleep(0)

    assert any(
        "desktop async action failed" in record.message
        for record in caplog.records
    )


async def test_shutdown_is_idempotent_and_closes_windows(
    desktop, monkeypatch
) -> None:
    await desktop.run()
    assert desktop.chat is not None
    await desktop.chat.wait_provider_refresh()

    monkeypatch.setattr(desktop.windows, "quit_application", lambda: None)

    await desktop.request_shutdown()
    await desktop.request_shutdown()

    assert desktop.core.shutdown_calls == 1
    assert desktop.windows.open_keys == ()
    assert desktop.chat.closing is True
