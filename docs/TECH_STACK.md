# JARVIS — Technology Stack

| Layer | Choice | Version | Rationale |
|---|---|---|---|
| Language | Python | 3.12 (3.12.10 in dev) | Project requirement; all deps support it |
| UI | PySide6 (Qt Widgets) | 6.12.0 | Native Windows desktop; custom painting, QSS, `QPropertyAnimation`; no web engine used |
| Async bridge | qasync | 0.28.0 | Binds asyncio and the Qt loop into one; no web server IPC |
| Async runtime | asyncio (stdlib) | — | Core engine, event bus, lifecycle |
| Configuration | pydantic-settings | 2.7.1 | Typed, env-first config with `JARVIS_` prefix |
| Database | PostgreSQL 16 + pgvector | docker | Relational + vector memory |
| DB driver | SQLAlchemy 2.0 async + asyncpg | 2.0.36 / 0.30.0 | Pooling, `pool_pre_ping`, async native |
| Migrations | Alembic | 1.14.0 | Versioned schema; embedding dimension fixed per DB |
| Vector type | custom SQLAlchemy `VECTOR` UserDefinedType | — | Avoids extra dependency for Phase 1; pgvector dialect types when needed (Phase 3) |
| Local LLM | Ollama (Qwen3 8B) via httpx client | Phase 2 (Stage 2.2) | Primary, private, local; native `/api/chat` NDJSON streaming, keep-alive configurable |
| Cloud LLM | Groq (OpenAI-compatible) via httpx | Phase 2 (Stage 2.3) | Optional, consent-gated; SSE streaming; key in DPAPI store, never in .env |
| Cloud consent + routing | `intelligence.router` + `ConsentManager` + `cloud_payload` | Phase 2 (Stage 2.4) | Local-first; cloud only with explicit per-request consent; strict two-message cloud payload; NOT implemented before Stage 2.4 |
| STT | faster-whisper | Phase 5 | Local, streaming, int8 |
| TTS | Piper | Phase 5 | Local, fast |
| Wake word | OpenWakeWord | Phase 5 | Custom "Jarvis" phrase |
| Vision | OpenCV, InsightFace, MediaPipe | Phase 6 | Lazy-loaded services |
| Packaging | PyInstaller | Phase 8 | One-dir build + installer |
| Testing | pytest, pytest-asyncio, pytest-qt | 8.3.4 / 0.24.0 / 4.5.0 | Unit, async, Qt smoke tests |
| Logging | stdlib `logging` + `RotatingFileHandler` | — | Zero extra deps; JSONL audit sink is stdlib too |
| Secrets | Windows DPAPI via `ctypes` (crypt32) | — | OS-native encryption, no third-party dependency |

## Explicitly prohibited

Next.js, React, HTML frontend, browser dashboards, Electron, WebView,
FastAPI, Flask, and any web server used as internal communication. The only
inter-module transport is the asyncio event bus plus the Qt signal bridge.

## Dependency policy

Install progressively, one phase at a time. Phase 1 adds only:
`PySide6`, `qasync`, `pytest-qt` (the rest were present already). Avoid new
dependencies unless a phase genuinely requires them.

## Local development services

- `database/docker-compose.yml` — PostgreSQL 16 with `pgvector` image,
  port 5432, volume for data persistence, `database/init/01_extensions.sql`
  creating the `vector` extension.
- `scripts/setup_db.py` — creates the database if needed, ensures
  extensions, runs `alembic upgrade head`.
- `scripts/backup_db.py` — `pg_dump` wrapper (foundation; scheduling in Phase 8).

## Configuration surface (`.env`, prefix `JARVIS_`)

| Variable | Default | Purpose |
|---|---|---|
| `JARVIS_ENVIRONMENT` | `development` | dev/prod behavior switches |
| `JARVIS_LOG_LEVEL` | `INFO` | Logging verbosity |
| `JARVIS_DATABASE_URL` | `postgresql+asyncpg://jarvis:jarvis@localhost:5432/jarvis` | Postgres DSN |
| `JARVIS_EMBEDDING_DIM` | `1024` | Baked into schema at migration time only |
| `JARVIS_OLLAMA_HOST` | `http://localhost:11434` | Ollama endpoint (Phase 2) |
| `JARVIS_OLLAMA_MODEL` | `qwen3:8b` | Default local model (Phase 2) |
| `JARVIS_GROQ_MODEL` | `llama-3.3-70b-versatile` | Cloud model (Phase 2) |
| `GROQ_API_KEY` | — | Secret — set via DPAPI credential store, not `.env`, in production |

Secrets: API keys are stored through `security/credentials.py` (DPAPI);
`.env` must never contain production secrets.
