# Test Database (`jarvis_test`)

Integration tests **must never** run against your primary `jarvis` database. Use an isolated `jarvis_test` database.

---

## Setup `jarvis_test`

```powershell
# Ensure jarvis-db container is running
docker compose -f database/docker-compose.yml up -d

# Create and migrate jarvis_test
python scripts/setup_db.py --database jarvis_test
```

---

## Running Integration Tests

Set `JARVIS_TEST_DATABASE_URL` in your PowerShell session:

```powershell
$env:JARVIS_TEST_DATABASE_URL = "postgresql+asyncpg://jarvis:jarvis@localhost:5433/jarvis_test"
python -m pytest tests/integration -v
```

> [!NOTE]
> If `JARVIS_TEST_DATABASE_URL` is unset or PostgreSQL is unreachable, PostgreSQL integration tests auto-skip. For release validation, `jarvis_test` must be active so PostgreSQL integration tests run without skipping.
