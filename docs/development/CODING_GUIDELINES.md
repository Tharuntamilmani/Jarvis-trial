# Coding Guidelines

All changes must comply with [AGENTS.md](../../AGENTS.md) and pass `ruff check .`.

---

## Architectural Boundaries

1. **No Qt in Core:** `src/jarvis/core/` and all domain services must never import `PySide6`.
2. **No Web Stacks:** Never introduce React, Next.js, HTML UIs, WebViews, FastAPI, Flask, or internal HTTP servers.
3. **Cloud Isolation:** Outbound cloud calls must go exclusively through `build_cloud_payload()` and `ConsentManager`.
4. **Secret Hygiene:** Never hardcode keys or store secrets in `.env` or `app_settings`; use `CredentialStore` (DPAPI) and `redact_audit_detail()` for logs.
5. **Lazy Optional Imports:** Keep `voice`, `vision`, and `telemetry` imports lazy so core startup remains fast when extras are absent.

---

## Python Style

- Target **Python 3.12+** with explicit type hints on functions, methods, and dataclasses.
- Keep modules focused and single-purpose; reuse existing `EventBus`, `ServiceRegistry`, and `DatabaseService` patterns.
- Write unit and integration tests for new behavior without modifying or weakening existing tests.
