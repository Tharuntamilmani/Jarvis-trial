"""Desktop application assembly (QApplication + qasync + core engine)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any

from PySide6.QtWidgets import QApplication

from jarvis.application import JarvisApplication
from jarvis.desktop.adapters.event_bridge import EventBridge
from jarvis.desktop.theme.theme import build_qss
from jarvis.desktop.tray import TrayService
from jarvis.desktop.viewmodels.app_state import AppState
from jarvis.desktop.viewmodels.chat_controller import ChatController
from jarvis.desktop.window_manager import Mode, WindowManager
from jarvis.monitoring.logging import get_logger

log = get_logger(__name__)


class DesktopApplication:
    """Owns the Qt application, the core engine, and their wiring."""

    def __init__(self, core: JarvisApplication, app: QApplication) -> None:
        self.core = core
        self.app = app
        self.state = AppState()
        self.windows = WindowManager(self.state)
        self.tray = TrayService(self.windows)
        self.bridge = EventBridge(core.engine.bus, self.state)
        self.chat: ChatController | None = None
        self._shutting_down = False
        self._tasks: set[asyncio.Task] = set()

        # Qt signals must never be connected directly to async methods: Qt
        # ignores the returned coroutine, so the action would never run.
        # Synchronous wrappers schedule the coroutine on the qasync loop.
        self.windows.shutdown_requested.connect(self._schedule_shutdown)
        self.tray.kill_switch_requested.connect(self._schedule_kill_switch)
        self.windows.mode_changed.connect(
            lambda mode: log.debug("mode switched: %s", mode)
        )

    @classmethod
    def create(cls) -> "DesktopApplication":
        """Build the QApplication (with qasync loop already installed)."""
        app = QApplication.instance() or QApplication([])
        app.setApplicationName("JARVIS")
        app.setQuitOnLastWindowClosed(False)  # closing windows → tray, not exit
        app.setStyleSheet(build_qss())
        core = JarvisApplication()
        return cls(core, app)

    # -- Startup ------------------------------------------------------------------

    async def run(self) -> None:
        """Start the engine, create the app-owned chat controller, show UI."""
        self.tray.show()
        self.tray.notify("JARVIS", "Core engine starting…")
        await self.core.start()

        # The controller outlives disposable Workspace windows, so the
        # conversation survives close/reopen and tray-only operation.
        chat_service = None
        consent = None
        try:
            chat_service = await self.core.engine.registry.get("chat")
            consent = await self.core.engine.registry.get("consent")
        except Exception:  # noqa: BLE001 - UI still loads without chat
            log.exception("chat services unavailable")
        self.chat = ChatController(
            self.state,
            chat_service,
            consent,
            provider_source=self.core.engine,
        )
        self.windows.attach_chat_controller(self.chat)

        self.windows.switch_mode(Mode.WORKSPACE)
        log.info("desktop application started")

    # -- Async-slot wrappers ----------------------------------------------------------

    def _schedule(self, coro: Awaitable[Any]) -> asyncio.Task | None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.warning("no running asyncio loop; UI action dropped")
            close = getattr(coro, "close", None)
            if close is not None:
                close()
            return None
        task = loop.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)
        return task

    def _on_task_done(self, task: asyncio.Task) -> None:
        """Retrieve task failures so qasync never reports them as unhandled."""
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            log.error(
                "desktop async action failed",
                exc_info=(type(error), error, error.__traceback__),
            )

    def _schedule_shutdown(self) -> None:
        self._schedule(self.request_shutdown())

    def _schedule_kill_switch(self) -> None:
        self._schedule(self._on_kill_switch())

    # -- Kill switch -----------------------------------------------------------------

    async def _on_kill_switch(self) -> None:
        if self.core.engine.kill_switch_active:
            await self.core.engine.rearm_kill_switch()
        else:
            await self.core.engine.trigger_kill_switch("manual: tray")

    # -- Shutdown -----------------------------------------------------------------------

    async def request_shutdown(self) -> None:
        """Graceful shutdown: chat teardown, engine, windows, Qt loop."""
        if self._shutting_down:
            return
        self._shutting_down = True
        log.info("application shutdown requested")

        # Stage 2.6 order: closing state → deny consent → cancel dispatch →
        # cancel the active chat → clear UI state → detach responder.
        if self.chat is not None:
            try:
                await self.chat.shutdown()
            except Exception:  # noqa: BLE001
                log.exception("chat controller shutdown failed")

        try:
            await self.core.shutdown()
        except Exception:  # noqa: BLE001
            log.exception("engine shutdown failed — forcing exit")
        finally:
            self.tray.hide()
            self.windows.shutdown()
            self.windows.quit_application()

    def about(self) -> dict[str, str]:
        return {
            "app": self.app.applicationName(),
            "engine_state": self.state.engine_state,
        }
