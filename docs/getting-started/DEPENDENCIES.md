# Dependencies

All Python package requirements are declared in [pyproject.toml](../../pyproject.toml) (`requires-python = ">=3.12"`).

---

## Core Dependencies

Installed by `python -m pip install -e .`:

| Package | Constraint | Role |
| :--- | :--- | :--- |
| `PySide6` | `>=6.8,<7` | Native Qt 6 desktop UI (HUD, Workspace, Floating, Tray) |
| `qasync` | `>=0.27,<1` | Bridges `asyncio` coroutines with the Qt event loop |
| `pydantic` | `>=2.7,<3` | Typed models and validation |
| `pydantic-settings` | `>=2.2,<3` | `JARVIS_` environment & `.env` configuration |
| `SQLAlchemy[asyncio]` | `>=2.0,<3` | Async ORM and connection pooling |
| `asyncpg` | `>=0.29,<1` | Async PostgreSQL driver |
| `alembic` | `>=1.13,<2` | Database schema migrations |
| `psycopg2-binary` | `>=2.9,<3` | Sync PostgreSQL driver for Alembic and admin scripts |
| `httpx` | `>=0.27,<1` | Async HTTP client for Ollama and Groq APIs |

---

## Optional Extras

Optional modules are **lazily imported** so startup remains fast and JARVIS still runs if an extra is not installed (checked by `check_optional_dependencies()` in `src/jarvis/monitoring/diagnostics.py` and `check_lazy_optional_imports()` in `src/jarvis/monitoring/performance.py`).

| Extra | Packages | Capability |
| :--- | :--- | :--- |
| `voice` | `sounddevice>=0.4`, `faster-whisper>=1.0`, `piper-tts>=1.2`, `openwakeword>=0.6` | Microphone I/O, local STT, local TTS, wake word |
| `vision` | `opencv-python>=4.9`, `insightface>=0.7`, `mediapipe>=0.10` | Opt-in camera, face recognition, hand gestures |
| `telemetry` | `psutil>=5.9` | Detailed CPU/RAM telemetry |
| `packaging` | `pyinstaller>=6.0` | Windows standalone bundle build |
| `dev` | `pytest>=8.0`, `pytest-asyncio>=0.24`, `pytest-qt>=4.4` | Unit, async, and Qt UI test suite |

---

## External Services

- **PostgreSQL 16 + pgvector:** `pgvector/pgvector:pg16` via Docker Compose (`database/docker-compose.yml`), plus `pg_dump` / `pg_restore` CLI tools for backup/restore.
- **Ollama:** Local LLM & embedding server (`http://localhost:11434`).
