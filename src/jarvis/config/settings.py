"""Configuration management for JARVIS.

Settings are typed via pydantic-settings and loaded from the environment
(variables prefixed with ``JARVIS_``) and an optional ``.env`` file.
Secrets (API keys) must NOT be placed here or in ``.env`` for production
use — they belong to the DPAPI credential store in
:mod:`jarvis.security.credentials`.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Environment(str, Enum):
    """Runtime environment of the application."""

    DEVELOPMENT = "development"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Typed application settings (environment-first)."""

    model_config = SettingsConfigDict(
        env_prefix="JARVIS_",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # Application
    app_name: str = "JARVIS"
    environment: Environment = Environment.DEVELOPMENT

    # Logging
    log_level: str = "INFO"
    log_dir: Path = PROJECT_ROOT / "logs"

    # Database
    database_url: str = (
        "postgresql+asyncpg://jarvis:jarvis@localhost:5432/jarvis"
    )
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_pool_recycle_seconds: int = 300

    # Embeddings — fixed per database; changed only via explicit migrations.
    embedding_dim: int = Field(default=1024, ge=64, le=65536)

    # Backup (foundation)
    backup_dir: Path = PROJECT_ROOT / "database" / "backups"
    backup_retention: int = Field(default=14, ge=1)

    # AI provider endpoints (Phase 2 clients; endpoints recorded now)
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"
    ollama_keep_alive: str = "10m"  # keep model warm between requests
    # Disable reasoning ("think") channel for thinking models like qwen3.
    # Reasoning otherwise consumes the token budget before any answer text.
    ollama_think: bool = False
    groq_model: str = "llama-3.3-70b-versatile"
    groq_api_base: str = "https://api.groq.com/openai/v1"

    # Security
    approval_timeout_seconds: float = Field(default=30.0, gt=0)
    audit_log_name: str = "audit.log"

    # Routing (Stage 2.4) — local-first, bounded retries, consent timeout
    routing_max_retries: int = Field(default=2, ge=0, le=5)
    routing_budget_seconds: float = Field(default=120.0, gt=0)
    routing_backoff_base_seconds: float = Field(default=0.5, gt=0)
    routing_backoff_max_seconds: float = Field(default=8.0, gt=0)
    cloud_consent_timeout_seconds: float = Field(default=30.0, gt=0)
    health_hard_down_ttl_seconds: float = Field(default=5.0, gt=0)

    # Chat orchestration (Stage 2.5)
    chat_max_concurrent_generations: int = Field(default=1, ge=1, le=16)
    chat_concurrency_policy: Literal["reject", "queue"] = "reject"
    chat_completed_cache_size: int = Field(default=64, ge=1, le=1024)

    # UI
    hotkey_quit: str = "Ctrl+Alt+Q"

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}")
        return upper

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION

    @property
    def audit_log_path(self) -> Path:
        return self.log_dir / self.audit_log_name


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings singleton."""
    return Settings()
