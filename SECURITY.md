# Security Policy — JARVIS 1.0.0

## Security Architecture Summary

JARVIS is designed around **local-first execution**, **fail-closed permissions**, **Win32 DPAPI secret protection**, and **append-only auditability**:

- **Credentials:** API keys (such as `groq_api_key`) are never stored in `.env`, `app_settings`, or logs. They are encrypted at rest per Windows user account using Win32 DPAPI (`CryptProtectData` / `CryptUnprotectData`) in `src/jarvis/security/credentials.py`.
- **Biometrics:** Camera capture is disabled on startup. Opt-in face enrollment stores embeddings encrypted with Win32 DPAPI (`.bio.bin` under `data/biometrics/` via `src/jarvis/security/biometrics.py`). Raw camera frames, face crops, and embeddings are never written to PostgreSQL, logs, or cloud payloads.
- **Cloud Payload Isolation:** `src/jarvis/intelligence/cloud_payload.py` enforces a strict 2-message outbound structure (static JARVIS system prompt + current user message) bound by SHA-256 fingerprint to a per-request `ConsentManager` decision.
- **Tool & Command Execution:** Governed by `PermissionPolicy`, `CommandAllowlist`, single-use `ApprovalGrant`s (SHA-256 fingerprint bound, 30s default timeout, fail-closed), and `ExecutionSandbox` with an automatic kill-switch threshold.
- **Audit Trail:** `AuditService` dual-writes redacted events to the PostgreSQL `audit_log` table (append-only enforced at database level) and `logs/audit.jsonl`.

For deep dives, see:
- [Security Overview](docs/security/SECURITY_OVERVIEW.md)
- [Credential Management](docs/security/CREDENTIAL_MANAGEMENT.md)
- [Cloud Data Policy](docs/security/CLOUD_DATA_POLICY.md)
- [Permissions & Approvals](docs/security/PERMISSIONS_AND_APPROVALS.md)

---

## Supported Versions

| Version | Supported | Notes |
| :--- | :--- | :--- |
| `1.0.0` | Yes | Current release on Windows 11 (Python `>=3.12`) |

---

## Reporting a Vulnerability

- **Contact:** *Needs verification* — no dedicated security contact email or advisory URL is currently configured in this repository.
- **Do not** open public issues containing API keys, `.env` contents, biometric files, or raw diagnostic logs.
- When reporting a security issue, include:
  - Affected module or file path
  - Reproduction steps and expected vs. actual behavior
  - JARVIS version (`1.0.0`) and Windows 11 build
