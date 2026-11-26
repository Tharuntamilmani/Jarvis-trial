"""Typed tool contracts and exceptions (Phase 7.1, zero PySide6)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
from typing import Any
import uuid

from jarvis.core.exceptions import JarvisError
from jarvis.security.permissions import Capability, RiskLevel

ToolCapability = Capability


class ToolRiskLevel(StrEnum):
    """Risk classification for registered tools."""

    READ_ONLY = "read_only"
    STATE_CHANGING = "state_changing"
    DESTRUCTIVE = "destructive"


def to_policy_risk(risk: ToolRiskLevel) -> RiskLevel:
    """Map tool risk level to Phase 1 PermissionPolicy RiskLevel."""
    if risk is ToolRiskLevel.READ_ONLY:
        return RiskLevel.SAFE
    if risk is ToolRiskLevel.STATE_CHANGING:
        return RiskLevel.ELEVATED
    return RiskLevel.DANGEROUS


class ToolExecutionError(JarvisError):
    """Base error for tool registration, validation, and sandbox execution."""


class ToolRegistrationError(ToolExecutionError):
    """Raised when tool registration violates registry rules."""


class ToolValidationError(ToolExecutionError):
    """Raised when a tool call or output violates its declared schema."""


class ToolPermissionDeniedError(ToolExecutionError):
    """Raised when PermissionPolicy or CommandAllowlist blocks execution."""


class ToolOutputLimitError(ToolExecutionError):
    """Raised when tool output exceeds max_output_bytes."""


@dataclass(frozen=True)
class ToolSpec:
    """Typed declaration of a single registered tool."""

    tool_id: str
    input_schema: dict[str, type[Any] | tuple[type[Any], ...]]
    output_schema: dict[str, type[Any] | tuple[type[Any], ...]] = field(
        default_factory=dict
    )
    capabilities: frozenset[ToolCapability] = frozenset()
    risk_level: ToolRiskLevel = ToolRiskLevel.READ_ONLY
    version: str = "1.0.0"
    description: str = ""
    optional_params: frozenset[str] = frozenset()
    timeout_seconds: float = 10.0
    max_output_bytes: int = 65_536
    requires_approval: bool | None = None
    supports_cancellation: bool = True
    command_param: str | None = None

    @property
    def approval_mandatory(self) -> bool:
        if self.requires_approval is not None:
            return self.requires_approval
        return self.risk_level in (
            ToolRiskLevel.STATE_CHANGING,
            ToolRiskLevel.DESTRUCTIVE,
        )


@dataclass(frozen=True)
class ToolCall:
    """Typed invocation request routed exclusively through ExecutionSandbox."""

    tool_id: str
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    actor_id: str = "user"
    agent_run_id: str | None = None
    version: str | None = None

    def canonical_fingerprint(self, resolved_version: str = "") -> str:
        """SHA-256 fingerprint binding approval to exact call, args & actor."""
        payload = {
            "tool_id": self.tool_id,
            "version": resolved_version or self.version or "",
            "actor_id": self.actor_id,
            "agent_run_id": self.agent_run_id or "",
            "arguments": self.arguments,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ToolExecutionContext:
    """Execution context passed into a validated tool handler."""

    call_id: str
    correlation_id: str
    actor_id: str
    agent_run_id: str | None = None
    approved: bool = False
    approval_fingerprint: str = ""


@dataclass(frozen=True)
class ToolResult:
    """Typed result returned by ExecutionSandbox."""

    call_id: str
    correlation_id: str
    tool_id: str
    success: bool
    output: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    duration_ms: float = 0.0
