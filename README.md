# JARVIS — Native Windows AI Desktop Assistant

Python 3.12 · PySide6 · PostgreSQL + pgvector · Phase 1 complete ·
Phase 2 in progress (native streaming Workspace chat, Stage 2.6).

## Quick start (development)

1. **Database** — start the pgvector container:

   ```
   docker compose -f database/docker-compose.yml up -d
   ```

2. **Configure** — copy `.env.example` to `.env` and set
   `JARVIS_DATABASE_URL` (the container publishes on port **5433**).

3. **Migrate** — create the schema:

   ```
   python scripts/setup_db.py
   ```

4. **Run**:

   ```
   python main.py
   ```

## What Phase 1 delivers

- Native PySide6 desktop shell with three modes: **HUD** (full-screen),
  **Workspace**, and **Floating assistant** — all bound to one shared state.
- Independent window lifecycles: hide ≠ close ≠ shutdown. The AI engine
  never restarts on mode switches; quitting happens only via the tray.
- Core engine (no Qt): async event bus, service registry, lifecycle manager,
  graceful startup/shutdown, live health checks.
- PostgreSQL via SQLAlchemy async (pooling, pre-ping, reconnect with
  backoff), Alembic baseline migration, `vector(1024)` embedding columns
  with HNSW indexes, append-only audit table.
- Security foundations: DPAPI credential store, kill switch
  (tray → Emergency Stop), permission policy, command allowlist,
  fail-closed approval service, append-only audit sink.
- Real Ollama/Groq providers behind a local-first router with strict
  two-message cloud isolation and per-request consent.
- Native Workspace chat: streaming bubbles, Stop, inline cloud-consent
  card, provider status, and an in-memory conversation that survives
  closing/reopening the Workspace window.

## Tests

```
python -m pytest tests -v
```

Integration tests auto-skip when no PostgreSQL is reachable
(`JARVIS_TEST_DATABASE_URL` to override).

## Documentation

- `docs/ARCHITECTURE.md` — full architecture (v2)
- `docs/PROJECT_STRUCTURE.md` — folders & module responsibilities
- `docs/ROADMAP.md` — phases and exit criteria
- `docs/TECH_STACK.md` — stack, versions, rationale
- `AGENTS.md` — master development instructions (binding)
