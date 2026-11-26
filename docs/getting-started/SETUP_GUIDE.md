# Setup Guide

After completing [Installation](INSTALLATION.md), configure your environment, initialize PostgreSQL, and optionally configure cloud fallback.

---

## 1. Create `.env`

Copy the template file `.env.example` to `.env`:

```powershell
Copy-Item .env.example .env
```

If you are using the Docker Compose container (`database/docker-compose.yml`), update `JARVIS_DATABASE_URL` in `.env` to use port **`5433`**:

```ini
JARVIS_DATABASE_URL=postgresql+asyncpg://jarvis:jarvis@localhost:5433/jarvis
```

> [!CAUTION]
> Never place API keys, passwords, or biometric data in `.env` or commit `.env` to version control.

---

## 2. Initialize the Database Schema

Run `scripts/setup_db.py` to create the target database if it does not exist and apply all Alembic migrations up to `head` (`0003_agent_runtime`):

```powershell
python scripts/setup_db.py
```

To target a different database name on the same server (for example, `jarvis_test`):

```powershell
python scripts/setup_db.py --database jarvis_test
```

---

## 3. Optional: Configure Groq Cloud Fallback (DPAPI)

JARVIS is local-first (Ollama). If you want to enable optional, per-request consented Groq fallback:

1. Obtain a Groq API key.
2. Import it into the **Win32 DPAPI credential store** using `scripts/import_groq_key.py` (never store it in `.env`):

```powershell
$env:GROQ_API_KEY = "gsk_your_key_here"
python scripts/import_groq_key.py
Remove-Item Env:GROQ_API_KEY
```

To overwrite an existing stored key explicitly:

```powershell
$env:GROQ_API_KEY = "gsk_your_new_key_here"
python scripts/import_groq_key.py --replace
Remove-Item Env:GROQ_API_KEY
```

---

## 4. Verify Setup

Run a quick diagnostics/packaging check or test suite before first launch:

```powershell
python -m pytest tests/unit -q
```

Proceed to [First Launch](FIRST_LAUNCH.md).
