"""Centralized application-level tool ExecutionSandbox (Phase 7.1, zero PySide6).

Note: ExecutionSandbox enforces policy, allowlist, approval, audit, timeout,
and output bounds. It is an application-level boundary, not an OS-level sandbox
for untrusted arbitrary code.
"""

from __future__ import annotations

import asyncio
import copy
import time
from typing import TYPE_CHECKING

from jarvis.core.exceptions import ApprovalDeniedError, KillSwitchActiveError
from jarvis.security.allowlist import CommandAllowlist
from jarvis.security.approval import ApprovalService
from jarvis.security.audit import AuditPersistenceError, AuditService, redact_audit_detail
from jarvis.security.permissions import PermissionPolicy, ToolPermission
from jarvis.tools.models import (
    ToolCall,
    ToolExecutionContext,
    ToolExecutionError,
    ToolOutputLimitError,
    ToolPermissionDeniedError,
    ToolResult,
    ToolRiskLevel,
    ToolValidationError,
    to_policy_risk,
)
from jarvis.tools.registry import ToolRegistry

if TYPE_CHECKING:  # pragma: no cover - typing only
    from jarvis.core.lifecycle import LifecycleManager


class ExecutionSandbox:
    """Single centralized boundary for all registered tool executions."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        policy: PermissionPolicy,
        allowlist: CommandAllowlist,
        approval: ApprovalService,
        audit: AuditService,
        lifecycle: LifecycleManager | None = None,
        max_concurrency: int = 4,
        require_db_audit: bool = False,
        auto_kill_switch_threshold: int = 3,
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._allowlist = allowlist
        self._approval = approval
        self._audit = audit
        self._lifecycle = lifecycle
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self._require_db_audit = require_db_audit
        self._auto_kill_threshold = max(1, int(auto_kill_switch_threshold))
        self._infra_failure_streak = 0

    async def execute(self, call: ToolCall) -> ToolResult:
        """Validate, authorize, audit, and execute a single typed ToolCall."""
        frozen_args = copy.deepcopy(call.arguments)
        call = ToolCall(
            tool_id=call.tool_id,
            arguments=frozen_args,
            call_id=call.call_id,
            correlation_id=call.correlation_id,
            actor_id=call.actor_id,
            agent_run_id=call.agent_run_id,
            version=call.version,
        )
        spec = self._registry.validate_call(call)
        handler = self._registry.get_handler(call.tool_id)
        base_detail = {
            "correlation_id": call.correlation_id,
            "call_id": call.call_id,
            "actor_id": call.actor_id,
            "agent_run_id": call.agent_run_id or "",
            "tool_id": call.tool_id,
            "risk_level": spec.risk_level.value,
        }

        if self._lifecycle is not None and self._lifecycle.kill_switch_active:
            await self._audit.record_async(
                call.tool_id,
                result="denied_kill_switch",
                detail=base_detail,
            )
            raise KillSwitchActiveError("emergency stop is engaged")

        perm = ToolPermission(
            tool=spec.tool_id,
            risk=to_policy_risk(spec.risk_level),
            capabilities=spec.capabilities,
        )
        if not self._policy.is_allowed(perm):
            await self._audit.record_async(
                call.tool_id,
                result="denied_permission",
                detail=base_detail,
            )
            raise ToolPermissionDeniedError(
                f"Tool {spec.tool_id!r} denied by PermissionPolicy"
            )

        if spec.command_param is not None:
            cmd = str(call.arguments.get(spec.command_param, ""))
            if not self._allowlist.is_allowed(cmd):
                await self._audit.record_async(
                    call.tool_id,
                    result="denied_allowlist",
                    detail=base_detail,
                )
                raise ToolPermissionDeniedError(
                    f"Command for {spec.tool_id!r} is not on CommandAllowlist"
                )

        fingerprint = call.canonical_fingerprint(spec.version)
        approved = False
        if spec.approval_mandatory:
            safe_args = redact_audit_detail(call.arguments)
            target = str(
                safe_args.get("path")
                or safe_args.get("dst")
                or safe_args.get("target")
                or safe_args.get("command")
                or ""
            )
            try:
                req_id = await self._approval.request(
                    action=spec.tool_id,
                    summary=f"{spec.tool_id} ({spec.risk_level.value})",
                    fingerprint=fingerprint,
                    tool_id=spec.tool_id,
                    risk_level=spec.risk_level.value,
                    target=target,
                    arguments_summary=str(safe_args)[:200],
                    agent_run_id=call.agent_run_id or "",
                )
                if not self._approval.verify_and_consume(req_id, fingerprint):
                    await self._record_infra_failure("approval_integrity_failed")
                    raise ApprovalDeniedError(
                        f"Approval integrity check failed for {spec.tool_id!r}"
                    )
                approved = True
            except ApprovalDeniedError:
                await self._audit.record_async(
                    call.tool_id,
                    tool=call.tool_id,
                    result="denied_approval",
                    detail=base_detail,
                )
                raise

        consequential = spec.risk_level in (
            ToolRiskLevel.STATE_CHANGING,
            ToolRiskLevel.DESTRUCTIVE,
        )
        try:
            await self._audit.record_async(
                call.tool_id,
                tool=call.tool_id,
                result="started",
                approved_by="user" if approved else "policy",
                detail=base_detail,
                require_db=consequential and self._require_db_audit,
            )
        except AuditPersistenceError:
            await self._record_infra_failure("audit_persistence_failed")
            raise

        ctx = ToolExecutionContext(
            call_id=call.call_id,
            correlation_id=call.correlation_id,
            actor_id=call.actor_id,
            agent_run_id=call.agent_run_id,
            approved=approved,
            approval_fingerprint=fingerprint,
        )
        started_at = time.perf_counter()
        try:
            async with self._semaphore:
                raw_output = await asyncio.wait_for(
                    handler(dict(call.arguments), ctx),
                    timeout=spec.timeout_seconds,
                )
            validated = self._registry.validate_output(spec, raw_output)
        except TimeoutError as exc:
            await self._audit.record_async(
                call.tool_id,
                tool=call.tool_id,
                result="timeout",
                detail=base_detail,
            )
            raise ToolExecutionError(
                f"Tool {spec.tool_id!r} timed out after {spec.timeout_seconds}s"
            ) from exc
        except asyncio.CancelledError:
            await self._audit.record_async(
                call.tool_id,
                tool=call.tool_id,
                result="cancelled",
                detail=base_detail,
            )
            raise
        except (
            ToolValidationError,
            ToolPermissionDeniedError,
            ToolOutputLimitError,
            ToolExecutionError,
        ) as exc:
            await self._audit.record_async(
                call.tool_id,
                tool=call.tool_id,
                result="denied_or_rejected",
                detail={**base_detail, "error": type(exc).__name__},
            )
            raise
        except Exception as exc:
            await self._audit.record_async(
                call.tool_id,
                tool=call.tool_id,
                result="failed",
                detail={**base_detail, "error": type(exc).__name__},
            )
            await self._record_infra_failure(f"tool_failed:{type(exc).__name__}")
            raise

        self._infra_failure_streak = 0
        duration_ms = (time.perf_counter() - started_at) * 1000.0
        await self._audit.record_async(
            call.tool_id,
            tool=call.tool_id,
            result="completed",
            approved_by="user" if approved else "policy",
            detail={**base_detail, "duration_ms": round(duration_ms, 2)},
        )
        return ToolResult(
            call_id=call.call_id,
            correlation_id=call.correlation_id,
            tool_id=call.tool_id,
            success=True,
            output=validated,
            duration_ms=duration_ms,
        )

    async def _record_infra_failure(self, reason: str) -> None:
        self._infra_failure_streak += 1
        if (
            self._infra_failure_streak >= self._auto_kill_threshold
            and self._lifecycle is not None
            and not self._lifecycle.kill_switch_active
        ):
            await self._lifecycle.trigger_kill_switch(
                f"auto_kill_switch:{reason}"
            )
