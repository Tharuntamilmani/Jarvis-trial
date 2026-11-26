"""Shared pytest fixtures for JARVIS tests."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from jarvis.config import Settings
from jarvis.monitoring.logging import reset_logging


@pytest.fixture(autouse=True)
def _isolate_jarvis_logger():
    """Prevent setup_logging() in one test from breaking caplog in later tests."""
    yield
    reset_logging()
    logging.getLogger("jarvis").propagate = True


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    """Isolated settings: temp log dir, secrets under tmp."""
    return Settings(
        log_dir=tmp_path / "logs",
        _env_file=None,  # type: ignore[call-arg]
    )
