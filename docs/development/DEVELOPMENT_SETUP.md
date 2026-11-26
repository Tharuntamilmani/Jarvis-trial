# Development Setup

Set up a local Windows 11 development environment for JARVIS 1.0.0.

---

## Prerequisites

- Windows 11
- Python `>=3.12`
- Docker Desktop (for `pgvector/pgvector:pg16`)
- Ollama (`qwen3:8b`, `nomic-embed-text`)

---

## Environment Setup (PowerShell)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

# Start PostgreSQL + pgvector
docker compose -f database/docker-compose.yml up -d

# Configure .env (port 5433) and initialize both dev & test databases
Copy-Item .env.example .env
python scripts/setup_db.py
python scripts/setup_db.py --database jarvis_test
```

---

## Verification

```powershell
ruff check .
$env:JARVIS_TEST_DATABASE_URL = "postgresql+asyncpg://jarvis:jarvis@localhost:5433/jarvis_test"
python -m pytest tests -v
```
