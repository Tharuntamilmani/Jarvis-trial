"""Shared pytest fixtures for JARVIS tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.config import Settings


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    """Isolated settings: temp log dir, secrets under tmp."""
    return Settings(
        log_dir=tmp_path / "logs",
        _env_file=None,  # type: ignore[call-arg]
    )
