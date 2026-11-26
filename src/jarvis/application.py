"""Core bootstrap (pre-Qt) for JARVIS.

Prepares settings, logging and the CoreEngine before the desktop layer is
assembled. Kept separate from ``jarvis.desktop`` so the engine can also be
driven headlessly (tests, CLI).
"""

from __future__ import annotations

from jarvis.config import Settings, get_settings
from jarvis.core.engine import CoreEngine
from jarvis.monitoring.logging import setup_logging


class JarvisApplication:
    """Application-level bootstrap without any UI dependency."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        safe_mode: bool = False,
    ) -> None:
        self.settings = settings or get_settings()
        setup_logging(
            level=self.settings.log_level, log_dir=self.settings.log_dir
        )
        self.engine = CoreEngine(self.settings, safe_mode=safe_mode)
        self.engine.assemble()

    async def start(self) -> None:
        await self.engine.start()

    async def shutdown(self) -> None:
        await self.engine.shutdown()
