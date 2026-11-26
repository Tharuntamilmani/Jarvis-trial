"""Unit tests for Phase 8.1 UserAppPreferences, PreferencesService & DPAPI isolation."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from jarvis.config import Settings
from jarvis.config.preferences import (
    PreferencePersistenceError,
    PreferencesService,
    UserAppPreferences,
)
from jarvis.security.credentials import CredentialStore


def test_preferences_validation_rejects_invalid_and_secret_values() -> None:
    # Default preferences valid
    prefs = UserAppPreferences()
    assert prefs.schema_version == 1
    assert prefs.backup_retention == 14

    # Invalid retention rejected
    with pytest.raises(ValidationError):
        UserAppPreferences(backup_retention=0)

    # Secrets accidentally passed into model fields rejected
    with pytest.raises(ValidationError, match="secret"):
        UserAppPreferences(ollama_model="gsk_supersecretkey123456")


@pytest.mark.asyncio
async def test_preferences_service_fail_closed_and_dpapi_credential_masking(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = Settings(
        log_dir=tmp_path / "logs",
        audit_log_path=tmp_path / "logs" / "audit.log",
    )
    creds = CredentialStore(settings)
    svc = PreferencesService(database=None, credentials=creds)

    # No DB -> load returns safe defaults; save fails closed instead of silent success
    loaded = await svc.load()
    assert loaded.default_provider == "auto"
    with pytest.raises(PreferencePersistenceError):
        await svc.save(UserAppPreferences(language="fr"))

    # DPAPI credential add, replace, status masking, delete (never logged)
    assert svc.credential_status("groq_api_key") == "Not configured"
    with caplog.at_level(logging.DEBUG):
        svc.set_api_key("groq_api_key", "gsk_test_secret_value_12345")
    assert svc.credential_status("groq_api_key") == "Configured"
    assert "gsk_test_secret_value_12345" not in caplog.text

    # Replace & delete
    svc.set_api_key("groq_api_key", "gsk_replaced_secret_67890")
    assert creds.load_secret("groq_api_key") == "gsk_replaced_secret_67890"
    assert svc.delete_api_key("groq_api_key") is True
    assert svc.credential_status("groq_api_key") == "Not configured"
