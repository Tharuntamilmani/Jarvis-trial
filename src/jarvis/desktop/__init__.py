"""Desktop application package for JARVIS (the only PySide6 consumer)."""

from jarvis.desktop.application import DesktopApplication
from jarvis.desktop.window_manager import Mode, WindowManager

__all__ = ["DesktopApplication", "Mode", "WindowManager"]
