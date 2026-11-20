"""Configuration tests."""

from __future__ import annotations

from jarvis.config import Settings


def test_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.app_name == "JARVIS"
    assert s.log_level == "INFO"
    assert s.embedding_dim == 1024
    assert s.ollama_host == "http://localhost:11434"


def test_invalid_log_level_rejected() -> None:
    import pytest

    with pytest.raises(Exception):
        Settings(_env_file=None, log_level="LOUD")


def test_embedding_dim_bounds() -> None:
    import pytest

    with pytest.raises(Exception):
        Settings(_env_file=None, embedding_dim=10)  # below minimum 64
