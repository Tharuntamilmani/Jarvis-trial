"""Tool permission policy primitives.

Phase 1 delivers the policy model and evaluation semantics (default-deny for
dangerous capabilities). Actual tool wiring and the security settings UI
arrive with Phase 7 — the policy engine is built and tested now so later
phases consume a stable contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class RiskLevel(StrEnum):
    SAFE = "safe"
    ELEVATED = "elevated"
    DANGEROUS = "dangerous"


class Capability(StrEnum):
    """System capabilities a tool may require."""

    FILESYSTEM_READ = "filesystem:read"
    FILESYSTEM_WRITE = "filesystem:write"
    PROCESS = "process"
    NETWORK = "network"
    SYSTEM_SETTINGS = "system:settings"
    CLIPBOARD = "clipboard"


@dataclass(frozen=True)
class ToolPermission:
    """Declaration of what a tool is and needs."""

    tool: str
    risk: RiskLevel
    capabilities: frozenset[Capability]


@dataclass
class PermissionPolicy:
    """Per-capability and per-tool permission decisions."""

    granted_capabilities: set[Capability] = field(default_factory=set)
    granted_tools: set[str] = field(default_factory=set)
    denied_tools: set[str] = field(default_factory=set)

    @classmethod
    def default_policy(cls) -> "PermissionPolicy":
        """Phase 1 default: only explicitly safe, local, no-approval caps."""
        return cls(
            granted_capabilities={
                Capability.CLIPBOARD,
            },
        )

    def is_allowed(self, permission: ToolPermission) -> bool:
        """Evaluate a tool request. Deny by default, especially dangerous."""
        if permission.tool in self.denied_tools:
            return False
        if permission.tool in self.granted_tools:
            return all(
                cap in self.granted_capabilities
                for cap in permission.capabilities
            )
        if permission.risk is RiskLevel.DANGEROUS:
            return False
        return all(
            cap in self.granted_capabilities for cap in permission.capabilities
        )
