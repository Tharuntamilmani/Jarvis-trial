# Database Migrations

Schema migrations are managed by **Alembic** (`alembic.ini`, `database/migrations/`).

---

## Verified Migration History

| Revision | Purpose |
| :--- | :--- |
| `0001_baseline` | Baseline schema: `users`, `conversations`, `messages`, `memories` (`vector(1024)` + HNSW), `episodes`, `user_preferences`, append-only `audit_log` |
| `0002_conversation_persistence` | Durable turn control & reconciliation fields |
| `0003_agent_runtime` | `agent_runs` and `app_settings` (`head`) |

---

## Common Commands (PowerShell)

```powershell
# Check current revision
python -m alembic current

# Upgrade to head
python -m alembic upgrade head

# Create a new migration after editing src/jarvis/database/models.py
python -m alembic revision --autogenerate -m "describe_change"
```

> [!IMPORTANT]
> `JARVIS_EMBEDDING_DIM` (`1024`) is fixed per database. Changing the vector dimension requires an explicit migration.
