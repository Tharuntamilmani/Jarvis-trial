"""Security foundations for JARVIS.

Phase 1 scope: credentials (DPAPI), kill switch, permission policy,
command allowlist, fail-closed approval service, append-only audit sink.
"""

from jarvis.security.allowlist import CommandAllowlist
from jarvis.security.approval import ApprovalService
from jarvis.security.audit import AuditService
from jarvis.security.credentials import CredentialStore
from jarvis.security.kill_switch import KillSwitchService
from jarvis.security.permissions import (
    Capability,
    PermissionPolicy,
    RiskLevel,
    ToolPermission,
)

__all__ = [
    "Capability",
    "CommandAllowlist",
    "ApprovalService",
    "AuditService",
    "CredentialStore",
    "KillSwitchService",
    "PermissionPolicy",
    "RiskLevel",
    "ToolPermission",
]
