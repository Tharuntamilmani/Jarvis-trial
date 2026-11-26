"""JARVIS application entry point.

Boot order:
1. Settings + logging (pre-Qt).
2. QApplication + qasync event loop (Qt and asyncio share one loop).
3. DesktopApplication: engine start → tray → default mode.

Shutdown only via tray → Quit (graceful engine shutdown first).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Allow running from a source checkout without installation.
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def main(argv: list[str] | None = None) -> int:
    import argparse

    import qasync

    from jarvis.desktop.application import DesktopApplication
    from jarvis.monitoring.logging import get_logger, setup_logging
    from jarvis.config import get_settings

    parser = argparse.ArgumentParser(description="JARVIS AI Desktop Assistant")
    parser.add_argument(
        "--safe-mode",
        action="store_true",
        help="Start with voice, vision, agents, and tools disabled for recovery",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    setup_logging(level=settings.log_level, log_dir=settings.log_dir)
    log = get_logger("entrypoint")
    log.info("JARVIS starting (safe_mode=%s)", args.safe_mode)

    app = DesktopApplication.create(
        safe_mode=args.safe_mode, enforce_single_instance=True
    )
    if not app.instance_lock.acquire():
        log.warning("another JARVIS instance is already running; exiting")
        return 1

    loop = qasync.QEventLoop(app.app)
    asyncio.set_event_loop(loop)

    async def run() -> None:
        await app.run()

    with loop:
        loop.run_until_complete(run())
        loop.run_forever()  # Qt loop keeps running until shutdown quits the app
    log.info("JARVIS exited cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
