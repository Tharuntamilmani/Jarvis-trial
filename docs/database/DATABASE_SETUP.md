# Database Setup

JARVIS uses **PostgreSQL 16** with **`pgvector`** (`pgvector/pgvector:pg16`).

---

## Starting the Development Container

`database/docker-compose.yml` defines the `jarvis-db` service mapped to **`localhost:5433`**:

```powershell
docker compose -f database/docker-compose.yml up -d
docker ps --filter "name=jarvis-db"
```

Default container credentials (development only):
- **User / Password / Database:** `jarvis` / `jarvis` / `jarvis`
- **Host Port:** `5433` (mapped to container `5432`)
- **Volume:** `jarvis_pgdata`

---

## Creating the Database & Applying Migrations

```powershell
python scripts/setup_db.py
```

`scripts/setup_db.py`:
1. Parses `JARVIS_DATABASE_URL` (or `--database NAME` override).
2. Connects via `psycopg2` to the `postgres` maintenance database and creates the target database if missing.
3. Runs `alembic upgrade head` using `alembic.ini`.
