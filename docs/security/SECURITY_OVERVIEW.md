# Security Overview

JARVIS 1.0.0 is engineered around **fail-closed defaults**, **local-first execution**, **Win32 DPAPI encryption**, and **append-only auditing** (`src/jarvis/security/`).

---

## Defense-in-Depth Controls

| Layer | Module | Guarantee |
| :--- | :--- | :--- |
| **Credentials** | `credentials.py` (`CredentialStore`) | Per-user Win32 DPAPI encryption; never stored in `.env`, DB, or logs |
| **Biometrics** | `biometrics.py` (`BiometricStore`) | Explicit consent; DPAPI-encrypted `.bio.bin`; never in DB or cloud |
| **Cloud Isolation** | `cloud_payload.py`, `consent.py` | 2-message payload only; per-request SHA-256 consent (30s fail-closed) |
| **Permissions & Allowlist** | `permissions.py`, `allowlist.py` | `RiskLevel` + `Capability` policy + `CommandAllowlist` |
| **Approvals** | `approval.py` (`ApprovalService`) | Single-use `ApprovalGrant` bound to SHA-256 fingerprint (30s fail-closed) |
| **Sandbox & Kill Switch** | `sandbox.py`, `kill_switch.py` | Bounded execution, `auto_kill_switch_threshold`, immediate emergency stop |
| **Audit Trail** | `audit.py` (`AuditService`) | Redacted dual-write to PostgreSQL `audit_log` (append-only) + `logs/audit.jsonl` |
