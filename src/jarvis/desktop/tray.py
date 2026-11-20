"""System tray integration.

The tray is the process anchor: it opens modes, triggers the kill switch,
and — critically — is the ONLY place the application can be quit.
Closing every window simply minimizes to tray.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from jarvis.desktop.window_manager import Mode, WindowManager


def _make_icon() -> QIcon:
    """Simple generated orb icon (no external asset needed)."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#00d4ff"))
    painter.setPen(QColor("#0a7ea4"))
    painter.drawEllipse(8, 8, 48, 48)
    painter.setBrush(QColor("#0d1729"))
    painter.drawEllipse(20, 20, 24, 24)
    painter.end()
    return QIcon(pixmap)


class TrayService(QObject):
    """System tray icon and menu."""

    kill_switch_requested = Signal()

    def __init__(self, windows: WindowManager) -> None:
        super().__init__()
        self._windows = windows
        self._tray = QSystemTrayIcon(_make_icon())
        self._tray.setToolTip("JARVIS")

        menu = QMenu()

        hud_action = QAction("HUD Mode", menu)
        hud_action.triggered.connect(lambda: windows.switch_mode(Mode.HUD))
        workspace_action = QAction("Workspace", menu)
        workspace_action.triggered.connect(
            lambda: windows.switch_mode(Mode.WORKSPACE)
        )
        floating_action = QAction("Floating Assistant", menu)
        floating_action.triggered.connect(
            lambda: windows.switch_mode(Mode.FLOATING)
        )

        menu.addAction(hud_action)
        menu.addAction(workspace_action)
        menu.addAction(floating_action)
        menu.addSeparator()

        kill_action = QAction("Emergency Stop (Kill Switch)", menu)
        kill_action.triggered.connect(self.kill_switch_requested.emit)
        menu.addAction(kill_action)

        menu.addSeparator()
        quit_action = QAction("Quit JARVIS", menu)
        quit_action.triggered.connect(windows.request_shutdown)
        menu.addAction(quit_action)

        self._tray.setContextMenu(menu)
        self._tray.setToolTip("JARVIS — AI Desktop Assistant")
        # Click on the tray icon opens the workspace.
        self._tray.activated.connect(self._on_activated)

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._windows.switch_mode(Mode.WORKSPACE)

    def show(self) -> None:
        self._tray.show()

    def notify(self, title: str, message: str) -> None:
        """Show a tray balloon notification."""
        self._tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 4000)

    def hide(self) -> None:
        self._tray.hide()

    @property
    def is_available(self) -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()
