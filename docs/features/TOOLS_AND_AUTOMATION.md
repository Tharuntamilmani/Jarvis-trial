# Tools & Automation

JARVIS exposes local system capabilities through `ToolRegistry` (`src/jarvis/tools/registry.py`) and enforces strict permission, allowlist, approval, and sandbox controls before execution.

---

## Built-In Tool Modules (`src/jarvis/tools/`)

| Module | Capability |
| :--- | :--- |
| `filesystem.py` | Scoped file reading/inspection tools |
| `system.py` | Safe system inspection & allowlisted actions |
| `clipboard.py` | Clipboard read/write helpers |
| `telemetry.py` | CPU/RAM/system telemetry (enhanced when `psutil` extra is installed) |

---

## Execution Pipeline

1. **Tool Input Validation:** Inputs are validated against typed `models.py` schemas.
2. **Permission & Allowlist Check:** `PermissionPolicy` (`RiskLevel`, `Capability`) and `CommandAllowlist` evaluate the requested action.
3. **Fail-Closed Approval:** Sensitive or high-risk actions require a single-use `ApprovalGrant` from `ApprovalService` (`src/jarvis/security/approval.py`), bound by SHA-256 fingerprint (`approval_timeout_seconds=30.0`).
4. **Sandboxed Execution:** `ExecutionSandbox` (`src/jarvis/security/sandbox.py`) enforces bounded execution and an `auto_kill_switch_threshold`.
5. **Append-Only Audit:** Every execution decision and outcome is redacted and recorded by `AuditService`.

See [Permissions & Approvals](../security/PERMISSIONS_AND_APPROVALS.md) for security details.
