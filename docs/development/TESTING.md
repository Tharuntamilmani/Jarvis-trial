# Testing Guide

Pytest is configured in [pyproject.toml](../../pyproject.toml) (`pythonpath = ["src"]`, `asyncio_mode = "auto"`).

---

## Running the Test Suite

```powershell
# Unit tests only
python -m pytest tests/unit -v

# Full suite with PostgreSQL integration tests (against jarvis_test)
$env:JARVIS_TEST_DATABASE_URL = "postgresql+asyncpg://jarvis:jarvis@localhost:5433/jarvis_test"
python -m pytest tests -v
```

---

## Optional Live Groq Quota Test

`tests/integration/test_groq_live.py` contains **2** opt-in live API tests that are **skipped by default** to protect cloud quota. To run them explicitly:

```powershell
$env:JARVIS_RUN_GROQ_LIVE = "1"
python -m pytest tests/integration/test_groq_live.py -v
```

Requires `groq_api_key` stored in Win32 DPAPI.

---

## Latest Verified 1.0.0 Test Baseline

- **Command:** `python -m pytest tests -v` (with `jarvis_test` active)
- **Result:** `562 passed, 0 failed, 2 skipped` (the 2 skipped tests are the opt-in `test_groq_live.py` tests)
- **Lint:** `ruff check .` passed with 0 errors
