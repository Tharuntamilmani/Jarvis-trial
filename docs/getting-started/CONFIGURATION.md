# Configuration Reference

JARVIS uses **two** configuration layers:

1. **Bootstrap & Environment Settings (`src/jarvis/config/settings.py`)** — Loaded via `pydantic-settings` from environment variables (`JARVIS_` prefix) and `.env`.
2. **User App Preferences (`src/jarvis/config/preferences.py`)** — Non-secret UI and runtime preferences persisted in PostgreSQL (`app_settings` table) via `PreferencesService` (`schema_version = 1`). Secrets such as `gsk_`, `sk-`, and `bearer ` tokens are explicitly rejected from preferences and belong in `CredentialStore` (DPAPI).

---

## Environment Variables (`JARVIS_` Prefix)

Verified fields in `Settings` (`src/jarvis/config/settings.py`):

| Variable | Default / Example | Purpose |
| :--- | :--- | :--- |
| `JARVIS_ENVIRONMENT` | `development` | Environment (`development`, `production`, etc.) |
| `JARVIS_LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) |
| `JARVIS_DATABASE_URL` | `postgresql+asyncpg://jarvis:jarvis@localhost:5432/jarvis` | Async PostgreSQL connection URL (use port `5433` for Docker Compose) |
| `JARVIS_TEST_DATABASE_URL` | *(unset)* | Isolated test database URL (e.g. `.../jarvis_test`) |
| `JARVIS_EMBEDDING_DIM` | `1024` | Fixed pgvector embedding dimension (changed only via migration) |
| `JARVIS_OLLAMA_HOST` | `http://localhost:11434` | Local Ollama server URL |
| `JARVIS_OLLAMA_MODEL` | `qwen3:8b` | Default local Ollama chat model |
| `JARVIS_OLLAMA_KEEP_ALIVE` | `10m` | Ollama model keep-alive duration |
| `JARVIS_OLLAMA_THINK` | `false` | Stream reasoning channel for thinking models (`qwen3`) |
| `JARVIS_OLLAMA_EMBEDDING_MODEL` | `nomic-embed-text` | Local Ollama embedding model |
| `JARVIS_GROQ_MODEL` | `llama-3.3-70b-versatile` | Default Groq cloud fallback model |
| `JARVIS_GROQ_API_BASE` | `https://api.groq.com/openai/v1` | Groq OpenAI-compatible base URL |
| `JARVIS_BACKUP_DIR` | *(project data backups dir)* | Default output directory for `scripts/backup_db.py` |
| `JARVIS_BACKUP_RETENTION` | `14` | Number of backup archives retained before pruning |
| `JARVIS_APPROVAL_TIMEOUT_SECONDS` | `30.0` | Fail-closed timeout for tool execution approval prompts |
| `JARVIS_CLOUD_CONSENT_TIMEOUT_SECONDS` | `30.0` | Fail-closed timeout for per-request cloud consent prompts |
| `JARVIS_HOTKEY_QUIT` | `Ctrl+Alt+Q` | Global quit shortcut |

> [!NOTE]
> Additional `Settings` fields (such as memory deduplication threshold `memory_dedup_threshold`, `db_persistence_deadline_seconds`, and `log_dir`) are defined with defaults in `src/jarvis/config/settings.py`. Inspect `src/jarvis/config/settings.py` directly if you need to tune internal timeouts.

---

## Secrets & Credentials

API keys are **never** configured via `JARVIS_` variables in `.env`. Use `scripts/import_groq_key.py` or the Settings UI to store `groq_api_key` in Win32 DPAPI (`src/jarvis/security/credentials.py`).
