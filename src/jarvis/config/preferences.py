"""Non-secret persistent user preferences backed by PostgreSQL app_settings (Phase 8.1).

Secrets (API keys) are strictly prohibited here and are routed exclusively to
Windows DPAPI via :class:`jarvis.security.credentials.CredentialStore`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, model_validator

from jarvis.core.exceptions import JarvisError
from jarvis.database.models import AppSetting
from jarvis.monitoring.logging import get_logger
from jarvis.security.credentials import CredentialStore

if TYPE_CHECKING:  # pragma: no cover - typing only
    from jarvis.database.connection import DatabaseService

log = get_logger(__name__)

_PREFS_KEY = "user_preferences"
_SECRET_PREFIXES = ("gsk_", "sk-", "bearer ")


class PreferencePersistenceError(JarvisError):
    """Raised when persistent preference storage fails."""


class UserAppPreferences(BaseModel):
    """Validated non-secret user preferences."""

    schema_version: int = Field(default=1, ge=1)

    # General & AI Models
    startup_with_windows: bool = False
    default_provider: Literal["auto", "ollama", "groq"] = "auto"
    language: str = Field(default="en", min_length=2, max_length=16)
    ollama_model: str = Field(default="qwen3:8b", min_length=1, max_length=120)
    groq_model: str = Field(
        default="llama-3.3-70b-versatile", min_length=1, max_length=120
    )

    # Appearance
    theme: Literal["dark", "midnight"] = "dark"
    accent_color: str = Field(default="#00d4ff", min_length=4, max_length=16)

    # Voice & Vision
    wake_word_enabled: bool = False
    tts_auto_speak: bool = False
    camera_opt_in: bool = False
    gesture_controls_enabled: bool = False

    # Shortcuts & Storage
    summon_hud_hotkey: str = Field(default="Ctrl+Shift+J", min_length=1, max_length=64)
    toggle_floating_hotkey: str = Field(
        default="Ctrl+Shift+F", min_length=1, max_length=64
    )
    backup_retention: int = Field(default=14, ge=1, le=365)

    @model_validator(mode="after")
    def _reject_secret_like_values(self) -> "UserAppPreferences":
        for field_name, val in self.model_dump().items():
            if isinstance(val, str):
                lowered = val.strip().lower()
                if any(lowered.startswith(prefix) for prefix in _SECRET_PREFIXES):
                    raise ValueError(
                        f"Field {field_name!r} appears to contain a secret; use CredentialStore"
                    )
        return self


class PreferencesService:
    """Manages non-secret preferences in PostgreSQL and secrets in DPAPI."""

    def __init__(
        self,
        *,
        database: DatabaseService | None = None,
        credentials: CredentialStore | None = None,
    ) -> None:
        self._db = database
        self._credentials = credentials

    def attach_database(self, database: DatabaseService | None) -> None:
        self._db = database

    async def load(self) -> UserAppPreferences:
        """Load preferences from ``app_settings``, falling back to defaults."""
        if self._db is None:
            return UserAppPreferences()
        try:
            async with self._db.session() as session:
                row = await session.get(AppSetting, _PREFS_KEY)
                if row is None or not isinstance(row.value, dict):
                    return UserAppPreferences()
                return UserAppPreferences.model_validate(row.value)
        except Exception:  # noqa: BLE001 - safe recovery to defaults
            log.warning("failed to load preferences; using defaults", exc_info=True)
            return UserAppPreferences()

    async def save(self, prefs: UserAppPreferences) -> UserAppPreferences:
        """Validate and persist non-secret preferences to ``app_settings``."""
        validated = UserAppPreferences.model_validate(prefs.model_dump())
        if self._db is None:
            raise PreferencePersistenceError(
                "Preferences database is unavailable"
            )
        try:
            async with self._db.session() as session:
                existing = await session.get(AppSetting, _PREFS_KEY)
                payload = validated.model_dump()
                if existing is None:
                    session.add(AppSetting(key=_PREFS_KEY, value=payload))
                else:
                    existing.value = payload
        except Exception as exc:
            raise PreferencePersistenceError(
                f"Failed to persist preferences: {type(exc).__name__}"
            ) from exc
        return validated

    # -- DPAPI Credential Management (never returns or logs secrets) -----------

    def credential_status(self, name: str) -> str:
        if self._credentials is None:
            return "Unavailable"
        return (
            "Configured"
            if name in self._credentials.list_secret_names()
            else "Not configured"
        )

    def set_api_key(self, name: str, secret: str) -> None:
        if self._credentials is None:
            raise PreferencePersistenceError("CredentialStore is unavailable")
        clean = secret.strip()
        if len(clean) < 8:
            raise ValueError("API key must be at least 8 characters")
        self._credentials.store_secret(name, clean)

    def delete_api_key(self, name: str) -> bool:
        if self._credentials is None:
            return False
        return self._credentials.delete_secret(name)
