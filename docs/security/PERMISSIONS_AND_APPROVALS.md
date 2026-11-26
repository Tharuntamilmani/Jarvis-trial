# Permissions & Approvals

Tool and command execution in JARVIS is governed by a fail-closed authorization pipeline (`src/jarvis/security/`).

---

## Authorization Pipeline

1. **`PermissionPolicy` (`permissions.py`):** Classifies actions by `RiskLevel` and `Capability`.
2. **`CommandAllowlist` (`allowlist.py`):** Rejects arbitrary generated shell commands; only explicitly allowlisted operations are permitted.
3. **`ApprovalService` (`approval.py`):**
   - Issues a single-use `ApprovalGrant` bound to the action's SHA-256 `fingerprint`.
   - Fails closed on missing responder, mismatch, or timeout (`approval_timeout_seconds = 30.0`).
   - Surfaced in the desktop UI via `approval_overlay.py`.
4. **`ExecutionSandbox` (`sandbox.py`):** Enforces bounded execution and triggers the kill switch if failures exceed `auto_kill_switch_threshold`.
5. **`AuditService` (`audit.py`):** Redacts sensitive fields (`redact_audit_detail`) and writes an append-only record to both PostgreSQL `audit_log` and `logs/audit.jsonl`.
