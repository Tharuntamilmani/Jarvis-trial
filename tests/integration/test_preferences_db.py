"""PostgreSQL integration test for Phase 8.1 app_settings persistence (jarvis_test)."""

from __future__ import annotations

import pytest

from jarvis.config.preferences import PreferencesService, UserAppPreferences

pytestmark = pytest.mark.usefixtures("migrated_database")


async def test_preferences_round_trip_in_jarvis_test(db_service) -> None:
    svc = PreferencesService(database=db_service)

    prefs = UserAppPreferences(
        default_provider="ollama",
        language="en",
        theme="midnight",
        ollama_model="qwen3:8b",
        backup_retention=21,
        summon_hud_hotkey="Ctrl+Alt+J",
    )
    saved = await svc.save(prefs)
    assert saved.backup_retention == 21

    loaded = await svc.load()
    assert loaded.default_provider == "ollama"
    assert loaded.theme == "midnight"
    assert loaded.backup_retention == 21
    assert loaded.summon_hud_hotkey == "Ctrl+Alt+J"
