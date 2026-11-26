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


async def test_startup_without_conversations_fails_closed(desktop) -> None:
    from jarvis.desktop.viewmodels.chat_models import STATUS_FAILED  # noqa: F401
    from jarvis.desktop.views.chat_view import ChatView
    from jarvis.desktop.window_manager import Mode

    await desktop.run()
    assert desktop.chat is not None
    await desktop.chat.wait_provider_refresh()

    assert desktop.chat.persistence_unavailable is True
    assert desktop.chat.can_send("hello") is False
    assert desktop.chat.send("hello") is None

    ws = desktop.windows.window(Mode.WORKSPACE)
    view = ws.findChild(ChatView)
    assert view is not None
    assert view.input.isEnabled() is False
    assert view.send_button.isEnabled() is False
    assert view.persistence_pill.isHidden() is False
    assert "persistence is unavailable" in view.error_label.text().lower()


async def test_startup_restores_active_conversation(qapp) -> None:
    from jarvis.conversations.models import ErrorCategory, safe_error_display
    from tests.ui.chat_test_helpers import (
        StubConversationPersistence,
        make_stored_message,
    )

    conv_id = "c" * 32
    stub = StubConversationPersistence()
    stub.active_conversation_id = conv_id
    stub.stored_messages[conv_id] = [
        make_stored_message(
            id="1" * 32,
            conversation_id=conv_id,
            role="user",
            content="restored question",
            seq=1,
            status="completed",
        ),
        make_stored_message(
            id="2" * 32,
            conversation_id=conv_id,
            role="assistant",
            content="partial",
            seq=2,
            status="failed",
            error=ErrorCategory.PROVIDER_ERROR.value,
        ),
    ]
    core = _FakeCore()
    core.engine.registry._values["conversations"] = stub  # noqa: SLF001
    app = DesktopApplication(core, qapp)
    try:
        await app.run()
        assert app.chat is not None
        await app.chat.wait_provider_refresh()
        assert app.chat.persistence_available is True
        assert app.chat.conversation_id == conv_id
        assert [m.content for m in app.chat.messages] == [
            "restored question",
            "partial",
        ]
        assert app.chat.messages[1].error_category == ErrorCategory.PROVIDER_ERROR.value
        assert app.chat.messages[1].error == safe_error_display(
            ErrorCategory.PROVIDER_ERROR.value
        )
    finally:
        app.windows.shutdown()
        app.tray.hide()


async def test_startup_active_lookup_failure_fails_closed(qapp) -> None:
    from jarvis.conversations.errors import PersistenceError
    from jarvis.desktop.views.chat_view import ChatView
    from jarvis.desktop.window_manager import Mode
    from jarvis.desktop.windows.workspace_window import WorkspaceWindow
    from tests.ui.chat_test_helpers import StubConversationPersistence

    stub = StubConversationPersistence()

    async def broken_lookup() -> str | None:
        raise PersistenceError("SECRET_INTERNAL_PG_DSN_LEAK")

    stub.get_active_conversation_id = broken_lookup  # type: ignore[method-assign]
    core = _FakeCore()
    core.engine.registry._values["conversations"] = stub  # noqa: SLF001
    app = DesktopApplication(core, qapp)
    try:
        await app.run()
        assert app.chat is not None
        await app.chat.wait_provider_refresh()

        assert app.chat.persistence_unavailable is True
        assert app.chat.can_send("hello") is False
        assert app.chat.send("hello") is None

        ws = app.windows.window(Mode.WORKSPACE)
        assert isinstance(ws, WorkspaceWindow)
        assert ws.new_chat_button.isEnabled() is False
        view = ws.findChild(ChatView)
        assert view is not None
        assert view.input.isEnabled() is False
        assert view.send_button.isEnabled() is False
        assert view.persistence_pill.isHidden() is False
        assert "SECRET_INTERNAL_PG_DSN_LEAK" not in view.error_label.text()
        assert "SECRET_INTERNAL_PG_DSN_LEAK" not in view.persistence_pill.text()
    finally:
        app.windows.shutdown()
        app.tray.hide()


async def test_startup_conversation_load_failure_fails_closed(qapp) -> None:
    from jarvis.conversations.errors import PersistenceTimeoutError
    from jarvis.desktop.views.chat_view import ChatView
    from jarvis.desktop.window_manager import Mode
    from jarvis.desktop.windows.workspace_window import WorkspaceWindow
    from tests.ui.chat_test_helpers import StubConversationPersistence

    stub = StubConversationPersistence()
    stub.active_conversation_id = "c" * 32

    async def broken_load(conversation_id: str, *, limit: int = 200):
        raise PersistenceTimeoutError("RAW_SQL_TIMEOUT_DETAIL")

    stub.load_conversation = broken_load  # type: ignore[method-assign]
    core = _FakeCore()
    core.engine.registry._values["conversations"] = stub  # noqa: SLF001
    app = DesktopApplication(core, qapp)
    try:
        await app.run()
        assert app.chat is not None
        await app.chat.wait_provider_refresh()

        assert app.chat.persistence_unavailable is True
        assert app.chat.can_send("hello") is False
        assert app.chat.messages == ()

        ws = app.windows.window(Mode.WORKSPACE)
        assert isinstance(ws, WorkspaceWindow)
        assert ws.new_chat_button.isEnabled() is False
        view = ws.findChild(ChatView)
        assert view is not None
        assert view.input.isEnabled() is False
        assert view.send_button.isEnabled() is False
        assert view.persistence_pill.isHidden() is False
        assert "RAW_SQL_TIMEOUT_DETAIL" not in view.error_label.text()
    finally:
        app.windows.shutdown()
        app.tray.hide()


async def test_startup_empty_database_without_active_conversation_is_healthy(
    qapp,
) -> None:
    from jarvis.desktop.views.chat_view import ChatView
    from jarvis.desktop.window_manager import Mode
    from jarvis.desktop.windows.workspace_window import WorkspaceWindow
    from tests.ui.chat_test_helpers import StubConversationPersistence

    stub = StubConversationPersistence()
    stub.active_conversation_id = None
    core = _FakeCore()
    core.engine.registry._values["conversations"] = stub  # noqa: SLF001
    app = DesktopApplication(core, qapp)
    try:
        await app.run()
        assert app.chat is not None
        await app.chat.wait_provider_refresh()

        assert app.chat.persistence_available is True
        assert app.chat.persistence_unavailable is False
        assert app.chat.messages == ()
        assert app.chat.conversation_id is None

        ws = app.windows.window(Mode.WORKSPACE)
        assert isinstance(ws, WorkspaceWindow)
        assert ws.new_chat_button.isEnabled() is True
        view = ws.findChild(ChatView)
        assert view is not None
        assert view.input.isEnabled() is True
        assert view.persistence_pill.isHidden() is True
    finally:
        app.windows.shutdown()
        app.tray.hide()


async def test_startup_reconcile_stale_failure_fails_closed(qapp) -> None:
    from jarvis.conversations.errors import PersistenceError
    from tests.ui.chat_test_helpers import StubConversationPersistence

    stub = StubConversationPersistence()

    async def broken_reconcile() -> int:
        raise PersistenceError("stale sweep failed")

    stub.reconcile_stale_generations = broken_reconcile  # type: ignore[method-assign]
    core = _FakeCore()
    core.engine.registry._values["conversations"] = stub  # noqa: SLF001
    app = DesktopApplication(core, qapp)
    try:
        await app.run()
        assert app.chat is not None
        await app.chat.wait_provider_refresh()
        assert app.chat.persistence_unavailable is True
        assert app.chat.can_send("hello") is False
    finally:
        app.windows.shutdown()
        app.tray.hide()
