# Contributing to JARVIS

Thank you for your interest in contributing to JARVIS. All contributions must follow the architecture and security rules defined in [AGENTS.md](AGENTS.md) and verified in this repository.

---

## Non-Negotiable Architecture Rules

1. **Native Desktop Only:** JARVIS is a native Windows 11 desktop application built with Python `>=3.12`, PySide6 (Qt Widgets, QSS, `QPropertyAnimation`), and `qasync`.
2. **Strictly Prohibited Technologies:** Do not introduce Next.js, React, HTML frontends, browser dashboards, FastAPI, Flask, Electron, WebView UIs, or an internal HTTP server.
3. **UI / Core Separation:** `src/jarvis/core/` and backend services must never import `PySide6`. The desktop layer communicates with backend services via typed Python interfaces and the asynchronous `EventBus`.
4. **Local-First & Privacy-Preserving:** Ollama is the primary provider. Cloud fallback (Groq) must remain fail-closed, per-request consented, and isolated to the static system prompt + current user message (`src/jarvis/intelligence/cloud_payload.py`).
5. **No Hardcoded Secrets:** Never commit `.env`, API keys, credentials, or biometric artifacts.

---

## Development Environment

See [docs/development/DEVELOPMENT_SETUP.md](docs/development/DEVELOPMENT_SETUP.md) for complete setup instructions.

```powershell
python -m pip install -e ".[dev]"
docker compose -f database/docker-compose.yml up -d
python scripts/setup_db.py
```

---

## Code Quality & Testing Expectations

Before opening a pull request or submitting changes:

1. **Type hints & docstrings:** Add Python type hints to new public functions/classes and keep functions modular.
2. **Lint:** Run `ruff check .` and ensure zero errors.
3. **Tests:** Run unit and integration tests against `jarvis_test` (never `jarvis` production data):

```powershell
$env:JARVIS_TEST_DATABASE_URL = "postgresql+asyncpg://jarvis:jarvis@localhost:5433/jarvis_test"
python -m pytest tests -v
```

4. **Migrations:** Any schema change must include an Alembic migration under `database/migrations/versions/` and a round-trip verification.
5. **Documentation:** Update relevant files in `docs/` when adding or changing configuration, commands, or subsystems.

See [docs/development/CODING_GUIDELINES.md](docs/development/CODING_GUIDELINES.md) and [docs/development/TESTING.md](docs/development/TESTING.md) for details.
