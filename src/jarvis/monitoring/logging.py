"""Logging setup for JARVIS.

Uses the standard library only: a rotating file handler under ``logs/`` and
a console handler. The audit sink (``jarvis.security.audit``) writes to its
own append-only file and must not be routed through arbitrary app loggers.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_ROOT_LOGGER = "jarvis"
_CONFIGURED_KEY = "_jarvis_logging_configured"


def setup_logging(
    level: str = "INFO",
    log_dir: Path | None = None,
    *,
    force: bool = False,
) -> logging.Logger:
    """Configure the ``jarvis`` root logger.

    Idempotent: repeated calls are no-ops unless ``force`` is set (used by
    tests to isolate handlers).
    """
    root = logging.getLogger(_ROOT_LOGGER)
    if getattr(root, _CONFIGURED_KEY, False) and not force:
        return root

    if log_dir is not None:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file: Path | None = log_dir / "jarvis.log"
    else:
        log_file = None

    root.setLevel(level.upper())
    root.propagate = False

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    console = logging.StreamHandler(stream=sys.stderr)
    console.setFormatter(formatter)
    root.addHandler(console)

    if log_file is not None:
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=2 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    setattr(root, _CONFIGURED_KEY, True)
    return root


def reset_logging() -> None:
    """Remove all handlers (test helper to restore isolation)."""
    root = logging.getLogger(_ROOT_LOGGER)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    if hasattr(root, _CONFIGURED_KEY):
        delattr(root, _CONFIGURED_KEY)


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger under the ``jarvis`` hierarchy."""
    if name == _ROOT_LOGGER or name.startswith(_ROOT_LOGGER + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_ROOT_LOGGER}.{name}")
