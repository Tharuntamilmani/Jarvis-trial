"""Independent window lifecycle management.

Semantics (strictly separated):

- ``hide_window(key)``   — window becomes invisible; state preserved; the
  window object stays alive and can be re-shown instantly.
- ``close_window(key)``  — window is closed and **destroyed**; the core
  engine and other windows are unaffected. Reopening creates a fresh window
  bound to the same shared AppState.
- ``shutdown()``         — application shutdown: graceful engine shutdown,
  tray removal, all windows destroyed, process exits. The ONLY path that
  stops the engine.

Mode switching is implemented purely via show/hide — the engine is never
restarted by mode changes.
"""

from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QWidget

from jarvis.desktop.viewmodels.app_state import AppState
from jarvis.desktop.windows.floating_window import FloatingWindow
from jarvis.desktop.windows.hud_window import HudWindow
from jarvis.desktop.windows.workspace_window import WorkspaceWindow
from jarvis.monitoring.logging import get_logger

log = get_logger(__name__)


class Mode(StrEnum):
    HUD = "hud"
    WORKSPACE = "workspace"
    FLOATING = "floating"


_WINDOW_KEYS = (Mode.HUD, Mode.WORKSPACE, Mode.FLOATING)


class WindowManager(QObject):
    """Creates, shows, hides and closes windows independently."""

    window_opened = Signal(str)
    window_hidden = Signal(str)
    window_closed = Signal(str)
    mode_changed = Signal(str)
    shutdown_requested = Signal()

    def __init__(
        self,
        state: AppState,
        chat_controller=None,
    ) -> None:
        super().__init__()
        self._state = state
        self._chat_controller = chat_controller
        self._windows: dict[str, QWidget] = {}

    def attach_chat_controller(self, controller) -> None:
        """Attach the app-owned chat controller before windows are created."""
        self._chat_controller = controller

    # -- Creation ----------------------------------------------------------------

    def _create(self, key: str) -> QWidget:
        if key == Mode.HUD:
            window: QWidget = HudWindow(self._state)
            window.hide_requested.connect(lambda: self.hide_window(Mode.HUD))
            window.switch_workspace_requested.connect(
                lambda: self.switch_mode(Mode.WORKSPACE)
            )
            window.switch_floating_requested.connect(
                lambda: self.switch_mode(Mode.FLOATING)
            )
        elif key == Mode.WORKSPACE:
            window = WorkspaceWindow(self._state, self._chat_controller)
            window.show_hud_requested.connect(
                lambda: self.switch_mode(Mode.HUD)
            )
            window.show_floating_requested.connect(
                lambda: self.switch_mode(Mode.FLOATING)
            )
            window.closed.connect(lambda: self.close_window(Mode.WORKSPACE))
        elif key == Mode.FLOATING:
            handler = (
                self._submit_quick_command
                if self._chat_controller is not None
                else None
            )
            window = FloatingWindow(
                self._state, command_handler=handler
            )
            window.expand_workspace_requested.connect(
                lambda: self.switch_mode(Mode.WORKSPACE)
            )
        else:
            raise KeyError(f"unknown window key: {key!r}")
        self._windows[key] = window
        log.debug("window created: %s", key)
        return window

    def window(self, key: str) -> QWidget:
        """Return the window instance, creating it if needed (not shown)."""
        return self._windows.get(key) or self._create(key)

    @property
    def open_keys(self) -> tuple[str, ...]:
        """Keys of windows that currently exist (visible or hidden)."""
        return tuple(self._windows)

    def is_open(self, key: str) -> bool:
        """True when the window object exists (visible or hidden)."""
        return key in self._windows

    def is_visible(self, key: str) -> bool:
        window = self._windows.get(key)
        return bool(window and window.isVisible())

    # -- Hiding / showing ------------------------------------------------------------

    def show_window(self, key: str) -> None:
        """Show (and create if necessary) a window without touching others."""
        window = self.window(key)
        if key == Mode.HUD:
            window.showFullScreen()
        else:
            window.show()
        window.activateWindow()
        self.window_opened.emit(key)

    def hide_window(self, key: str) -> None:
        """Hide a window; state is preserved, the object stays alive."""
        window = self._windows.get(key)
        if window is not None and window.isVisible():
            window.hide()
            self.window_hidden.emit(key)

    # -- Closing ------------------------------------------------------------------------

    def close_window(self, key: str) -> None:
        """Close and destroy one window. Engine and other windows unaffected."""
        window = self._windows.pop(key, None)
        if window is None:
            return
        window.close()
        window.deleteLater()
        self.window_closed.emit(key)
        log.debug("window closed: %s", key)

    def close_all(self) -> None:
        """Close every window (used by shutdown only)."""
        for key in list(self._windows):
            self.close_window(key)

    # -- Floating quick command ------------------------------------------------------------

    def _submit_quick_command(self, text: str) -> bool:
        """Route a floating quick command through the existing chat path.

        This is deliberately not a command executor: it only submits text to
        ChatController.send(), which enforces availability and kill-switch
        policy. The text is left intact when the request is rejected.
        """
        controller = self._chat_controller
        if controller is None or not controller.service_available:
            return False
        if not controller.can_send(text):
            return False
        if controller.send(text) is None:
            return False
        self.switch_mode(Mode.WORKSPACE)
        return True

    # -- Mode switching ------------------------------------------------------------------

    def switch_mode(self, mode: str) -> None:
        """Switch to a mode = pure visibility change. Never restarts anything."""
        for key in _WINDOW_KEYS:
            if key == mode:
                self.show_window(key)
            else:
                self.hide_window(key)
        self.mode_changed.emit(mode)

    # -- Shutdown -------------------------------------------------------------------------

    def request_shutdown(self) -> None:
        """Ask the application to shut down gracefully."""
        self.shutdown_requested.emit()

    def shutdown(self) -> None:
        """Destroy all windows (called after the engine has shut down)."""
        self.close_all()

    def quit_application(self) -> None:
        """Quit the Qt application loop (after shutdown())."""
        app = QApplication.instance()
        if app is not None:
            app.quit()
