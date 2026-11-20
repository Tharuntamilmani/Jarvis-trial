"""Credential import script behavior (Stage 2.3).

Runs ``scripts/import_groq_key.py`` as a subprocess against an isolated
DPAPI store (temp log dir ⇒ temp secrets dir) to verify the
never-overwrite-silently precedence rules.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from jarvis.config import Settings
from jarvis.security.credentials import CredentialStore

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "import_groq_key.py"

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows DPAPI required"
)


def _run(env_key: str | None, log_dir: Path, *extra: str):
    env = os.environ.copy()
    env["JARVIS_LOG_DIR"] = str(log_dir)
    env.pop("GROQ_API_KEY", None)
    if env_key is not None:
        env["GROQ_API_KEY"] = env_key
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), *extra],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


@pytest.fixture()
def isolated_store(tmp_path):
    log_dir = tmp_path / "logs"
    settings = Settings(log_dir=log_dir, _env_file=None)  # type: ignore[call-arg]
    store = CredentialStore(settings)
    yield store
    for name in store.list_secret_names():
        store.delete_secret(name)


def test_import_from_env_then_no_silent_overwrite(tmp_path, isolated_store) -> None:
    # 1) No key anywhere → clean not-configured state.
    result = _run(None, tmp_path / "logs")
    assert result.returncode == 1
    assert isolated_store.load_secret("groq_api_key") is None

    # 2) Key in environment → imported.
    result = _run("gsk_new_key", tmp_path / "logs")
    assert result.returncode == 0
    assert isolated_store.load_secret("groq_api_key") == "gsk_new_key"
    assert "gsk_new_key" not in result.stdout  # key never printed

    # 3) Existing key + env key present → UNCHANGED without --replace.
    result = _run("gsk_other_key", tmp_path / "logs")
    assert result.returncode == 0
    assert "already stored" in result.stdout
    assert isolated_store.load_secret("groq_api_key") == "gsk_new_key"
    assert "gsk_other_key" not in result.stdout


def test_explicit_replace_overwrites(tmp_path, isolated_store) -> None:
    isolated_store.store_secret("groq_api_key", "gsk_old_key")
    result = _run("gsk_rotated_key", tmp_path / "logs", "--replace")
    assert result.returncode == 0
    assert isolated_store.load_secret("groq_api_key") == "gsk_rotated_key"
    assert "gsk_rotated_key" not in result.stdout

    # --replace WITHOUT a new key is refused (no destructive no-op).
    result = _run(None, tmp_path / "logs", "--replace")
    assert result.returncode == 1
    assert isolated_store.load_secret("groq_api_key") == "gsk_rotated_key"
