"""Desktop application assembly (QApplication + qasync + core engine)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any

from PySide6.QtWidgets import QApplication

from jarvis.application import JarvisApplication
from jarvis.core.recovery import CrashRecoveryManager
from jarvis.core.single_instance import SingleInstanceLock
from jarvis.desktop.adapters.event_bridge import EventBridge
from jarvis.desktop.hotkeys import GlobalHotkeyManager
from jarvis.desktop.theme.theme import build_qss
from jarvis.desktop.tray import TrayService
from jarvis.desktop.viewmodels.app_state import AppState
from jarvis.desktop.viewmodels.chat_controller import ChatController
from jarvis.desktop.viewmodels.security_controller import SecurityController
from jarvis.desktop.viewmodels.vision_controller import VisionController
from jarvis.desktop.viewmodels.voice_controller import VoiceController
from jarvis.desktop.views.approval_overlay import ApprovalOverlay
from jarvis.desktop.window_manager import Mode, WindowManager
from jarvis.monitoring.logging import get_logger

log = get_logger(__name__)


class DesktopApplication:
    """Owns the Qt application, the core engine, and their wiring."""

    def __init__(
        self,
        core: JarvisApplication,
        app: QApplication,
        *,
        enforce_single_instance: bool = False,
    ) -> None:
        self.core = core
        self.app = app
        self._enforce_single_instance = enforce_single_instance
        self.state = AppState()
        self.windows = WindowManager(self.state)
        self.tray = TrayService(self.windows)
        self.bridge = EventBridge(core.engine.bus, self.state)
        self.chat: ChatController | None = None
        self.voice = VoiceController(self.state)
        self.vision = VisionController(self.state)
        self.hotkeys = GlobalHotkeyManager()
        self.instance_lock = SingleInstanceLock()
        self.approval_overlay = ApprovalOverlay(
            self.state, on_decision=self._on_approval_decision
        )
        self._approval_service = None
        self._shutting_down = False
        self._tasks: set[asyncio.Task] = set()

        # Qt signals must never be connected directly to async methods: Qt
        # ignores the returned coroutine, so the action would never run.
        # Synchronous wrappers schedule the coroutine on the qasync loop.
        self.windows.shutdown_requested.connect(self._schedule_shutdown)
        self.tray.kill_switch_requested.connect(self._schedule_kill_switch)
        self.tray.voice_toggle_requested.connect(
            lambda: self.voice.set_wake_word_enabled(
                not self.state.voice_wake_word_enabled
            )
        )
        self.tray.camera_toggle_requested.connect(
            lambda: self.vision.set_camera_enabled(
                not self.state.vision_camera_active
            )
        )
        self.state.notification.connect(self.tray.notify_event)
        self.app.installNativeEventFilter(self.hotkeys)
        self.hotkeys.register_hotkey(
            "summon_hud", "Ctrl+Shift+J", lambda: self.windows.switch_mode(Mode.HUD)
        )
        self.hotkeys.register_hotkey(
            "toggle_floating",
            "Ctrl+Shift+F",
            lambda: self.windows.switch_mode(Mode.FLOATING),
        )
        self.windows.mode_changed.connect(
            lambda mode: log.debug("mode switched: %s", mode)
        )

    @classmethod
    def create(
        cls,
        *,
        safe_mode: bool = False,
        enforce_single_instance: bool = True,
    ) -> "DesktopApplication":
        """Build the QApplication (with qasync loop already installed)."""
        app = QApplication.instance() or QApplication([])
        app.setApplicationName("JARVIS")
        app.setQuitOnLastWindowClosed(False)  # closing windows → tray, not exit
        app.setStyleSheet(build_qss())
        core = JarvisApplication(safe_mode=safe_mode)
        return cls(core, app, enforce_single_instance=enforce_single_instance)

    # -- Startup ------------------------------------------------------------------

    async def run(self) -> None:
        """Start the engine, create the app-owned chat controller, show UI."""
        if self._enforce_single_instance and not self.instance_lock.acquire():
            log.warning("another JARVIS instance is already running")
            return
        self.tray.show()
        self.tray.notify("JARVIS", "Core engine starting…")
        await self.core.start()

        # The controller outlives disposable Workspace windows, so the
        # conversation survives close/reopen and tray-only operation.
        chat_service = None
        consent = None
        conversations = None
        persistence_failed = False
        try:
            chat_service = await self.core.engine.registry.get("chat")
            consent = await self.core.engine.registry.get("consent")
        except Exception:  # noqa: BLE001 - UI still loads without chat
            log.exception("chat services unavailable")
        try:
            conversations = await self.core.engine.registry.get("conversations")
        except Exception:  # noqa: BLE001 - never crash the shell
            log.exception("conversation persistence unavailable")
            persistence_failed = True
        self.chat = ChatController(
            self.state,
            chat_service,
            consent,
            provider_source=self.core.engine,
            conversations=conversations,
        )
        if persistence_failed or conversations is None:
            self.chat.mark_persistence_unavailable()
            self.tray.notify(
                "JARVIS",
                "Conversation persistence is unavailable. Messages cannot "
                "be sent until the database is reachable.",
            )
        else:
            # Reconcile stale open generations from prior sessions, then
            # restore the active conversation.
            try:
                reconcile_stale = getattr(
                    conversations, "reconcile_stale_generations", None
                )
                if reconcile_stale is not None:
                    await reconcile_stale()
                active_id = await conversations.get_active_conversation_id()
                if active_id:
                    await self.chat.load_conversation(
                        active_id, raise_on_error=True
                    )
                await self.chat.refresh_conversations()
            except Exception:  # noqa: BLE001 - fail closed if DB unreachable
                log.exception("conversation restoration failed")
                self.chat.mark_persistence_unavailable()
                self.tray.notify(
                    "JARVIS",
                    "Conversation persistence is unavailable. Messages cannot "
                    "be sent until the database is reachable.",
                )

        self.windows.attach_chat_controller(self.chat)
        self.voice.attach_chat_controller(self.chat)
        try:
            voice_service = await self.core.engine.registry.get("voice")
            self.voice.attach_voice_service(voice_service)
        except Exception:  # noqa: BLE001 - UI still loads without voice
            log.exception("voice service unavailable")
        self.windows.attach_voice_controller(self.voice)
        try:
            vision_service = await self.core.engine.registry.get("vision")
            self.vision.attach_vision_service(vision_service)
        except Exception:  # noqa: BLE001 - UI still loads without vision
            log.exception("vision service unavailable")
        self.vision.toggle_hud_requested.connect(
            lambda: self.windows.switch_mode(Mode.HUD)
        )
        self.vision.toggle_floating_requested.connect(
            lambda: self.windows.switch_mode(Mode.FLOATING)
        )
        self.vision.stop_speaking_requested.connect(self.voice.stop_speaking)
        self.windows.attach_vision_controller(self.vision)
        try:
            sec_ctrl = SecurityController(
                self.state,
                policy=self.core.engine.registry.try_get("permissions"),
                allowlist=self.core.engine.registry.try_get("allowlist"),
                audit=self.core.engine.registry.try_get("audit"),
                registry=self.core.engine.registry.try_get("tools"),
                lifecycle=self.core.engine.lifecycle,
            )
            self.windows.attach_security_controller(sec_ctrl)
        except Exception:  # noqa: BLE001 - UI still loads if optional services missing
            log.exception("security controller setup failed")
        try:
            prefs_svc = await self.core.engine.registry.get("preferences")
            self.windows.attach_preferences_service(prefs_svc)
        except Exception:  # noqa: BLE001
            log.exception("preferences service setup failed")

        try:
            approval_svc = await self.core.engine.registry.get("approval")
            self._approval_service = approval_svc
            if approval_svc is not None:
                approval_svc.add_responder(self._await_ui_approval)
        except Exception:  # noqa: BLE001
            log.exception("approval service setup failed")

        try:
            audit_svc = await self.core.engine.registry.get("audit")
            await CrashRecoveryManager(
                audit=audit_svc, conversations=conversations
            ).reconcile()
        except Exception:  # noqa: BLE001
            log.exception("crash recovery reconciliation failed")

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

    async def _await_ui_approval(self, event) -> bool:
        if self._approval_service is None:
            return False
        future = self._approval_service._pending.get(event.request_id)  # noqa: SLF001
        if future is None:
            return False
        return bool(await future)

    def _on_approval_decision(self, request_id: str, approved: bool) -> None:
        if self._approval_service is not None:
            self._approval_service.resolve_pending(request_id, approved)

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
            self.hotkeys.unregister_all()
            self.app.removeNativeEventFilter(self.hotkeys)
            self.tray.hide()
            self.windows.shutdown()
            self.instance_lock.release()
            self.windows.quit_application()

    def about(self) -> dict[str, str]:
        return {
            "app": self.app.applicationName(),
            "engine_state": self.state.engine_state,
        }
