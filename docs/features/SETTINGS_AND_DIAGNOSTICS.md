# Settings & Diagnostics

---

## Persisted Preferences (`src/jarvis/config/preferences.py`)

`PreferencesService` manages `UserAppPreferences` (`schema_version = 1`) stored in PostgreSQL `app_settings`:

- Stores non-secret UI, model, voice, vision, and Windows startup preferences.
- Explicitly rejects secret-like strings (`gsk_`, `sk-`, `bearer `).
- API keys are routed strictly to `CredentialStore` (Win32 DPAPI).

---

## Privacy-Safe Diagnostics (`src/jarvis/monitoring/diagnostics.py`)

`DiagnosticsService.collect()` gathers operational health across:

- `DatabaseService` connectivity
- `check_optional_dependencies()` (lazy spec check without eager imports)
- `VoiceDiagnostics`, `VisionDiagnostics`, `AgentDiagnostics`, and `LifecycleManager` health
- `StartupProfiler` timings (`src/jarvis/monitoring/performance.py`)

All exported diagnostic payloads pass through `redact_audit_detail()` (`src/jarvis/security/audit.py`) so API keys, tokens, and secrets are never exported.

---

## Application & Audit Logs

- **Application log:** Rotating file under `logs/jarvis.log` (`2 MB`, `5` backups) + stderr console (`src/jarvis/monitoring/logging.py`).
- **Audit log:** Redacted append-only trail in PostgreSQL `audit_log` and `logs/audit.jsonl`.
