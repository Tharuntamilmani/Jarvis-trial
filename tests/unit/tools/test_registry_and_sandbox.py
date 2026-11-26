"""Unit tests for Phase 7.1 Typed Tool Registry & ExecutionSandbox."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from jarvis.config import Settings
from jarvis.core.event_bus import EventBus
from jarvis.core.exceptions import ApprovalDeniedError, KillSwitchActiveError
from jarvis.core.lifecycle import LifecycleManager
from jarvis.core.service_registry import ServiceRegistry
from jarvis.security.allowlist import CommandAllowlist
from jarvis.security.approval import ApprovalService
from jarvis.security.audit import AuditService
from jarvis.security.permissions import Capability, PermissionPolicy
from jarvis.security.sandbox import ExecutionSandbox
from jarvis.tools import (
    ToolCall,
    ToolExecutionError,
    ToolOutputLimitError,
    ToolPermissionDeniedError,
    ToolRegistrationError,
    ToolRegistry,
    ToolRiskLevel,
    ToolSpec,
    ToolValidationError,
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        log_dir=tmp_path / "logs",
        audit_log_path=tmp_path / "logs" / "audit.log",
        approval_timeout_seconds=0.5,
    )


def _sandbox(
    tmp_path: Path,
    *,
    policy: PermissionPolicy | None = None,
    allowlist: CommandAllowlist | None = None,
    approve: bool | None = None,
) -> tuple[ExecutionSandbox, ToolRegistry, AuditService, LifecycleManager]:
    bus = EventBus()
    settings = _settings(tmp_path)
    lifecycle = LifecycleManager(ServiceRegistry(), bus)
    audit = AuditService(settings, bus)
    approval = ApprovalService(settings, bus)
    if approve is not None:
        approval.add_responder(lambda _ev: asyncio.sleep(0, result=approve))
    registry = ToolRegistry()
    sandbox = ExecutionSandbox(
        registry=registry,
        policy=policy or PermissionPolicy(granted_capabilities={Capability.FILESYSTEM_READ}),
        allowlist=allowlist or CommandAllowlist(),
        approval=approval,
        audit=audit,
        lifecycle=lifecycle,
        max_concurrency=2,
    )
    return sandbox, registry, audit, lifecycle


def test_tools_and_sandbox_never_import_pyside6() -> None:
    assert not any(
        (name.startswith("jarvis.tools") or name.startswith("jarvis.security"))
        and "PySide6" in name
        for name in sys.modules
    )


def test_registry_duplicate_agent_escalation_and_schema_validation() -> None:
    reg = ToolRegistry()
    spec = ToolSpec(
        tool_id="fs.read",
        input_schema={"path": str},
        output_schema={"content": str},
        capabilities=frozenset({Capability.FILESYSTEM_READ}),
        risk_level=ToolRiskLevel.READ_ONLY,
    )

    async def _handler(args, ctx):
        del args, ctx
        return {"content": "ok"}

    # Agents cannot register tools or escalate capabilities
    with pytest.raises(ToolRegistrationError, match="Agents"):
        reg.register(spec, _handler, caller_is_agent=True)

    reg.register(spec, _handler)
    with pytest.raises(ToolRegistrationError, match="already registered"):
        reg.register(spec, _handler)

    # Unknown tool rejected
    with pytest.raises(ToolExecutionError, match="unknown"):
        reg.validate_call(ToolCall(tool_id="does.not.exist", arguments={}))

    # Undeclared parameter rejected
    with pytest.raises(ToolValidationError, match="undeclared"):
        reg.validate_call(
            ToolCall(tool_id="fs.read", arguments={"path": "a.txt", "extra": 1})
        )

    # Wrong type rejected
    with pytest.raises(ToolValidationError, match="type"):
        reg.validate_call(ToolCall(tool_id="fs.read", arguments={"path": 123}))


@pytest.mark.asyncio
async def test_sandbox_enforces_policy_approval_killswitch_timeout_and_output(
    tmp_path: Path,
) -> None:
    sandbox, reg, audit, lifecycle = _sandbox(
        tmp_path,
        policy=PermissionPolicy(
            granted_capabilities={
                Capability.FILESYSTEM_READ,
                Capability.FILESYSTEM_WRITE,
            }
        ),
        approve=False,
    )

    executed: list[str] = []

    async def _read(args, ctx):
        executed.append(f"read:{ctx.correlation_id}")
        return {"content": args["path"]}

    async def _delete(args, ctx):
        executed.append(f"delete:{args['path']}")
        return {"deleted": True}

    async def _slow(args, ctx):
        del args, ctx
        await asyncio.sleep(0.5)
        return {"ok": True}

    async def _huge(args, ctx):
        del args, ctx
        return {"content": "x" * 2048}

    reg.register(
        ToolSpec(
            tool_id="fs.read",
            input_schema={"path": str},
            output_schema={"content": str},
            capabilities=frozenset({Capability.FILESYSTEM_READ}),
            risk_level=ToolRiskLevel.READ_ONLY,
        ),
        _read,
    )
    reg.register(
        ToolSpec(
            tool_id="fs.delete",
            input_schema={"path": str},
            output_schema={"deleted": bool},
            capabilities=frozenset({Capability.FILESYSTEM_WRITE}),
            risk_level=ToolRiskLevel.DESTRUCTIVE,
        ),
        _delete,
    )
    reg.register(
        ToolSpec(
            tool_id="sys.slow",
            input_schema={},
            output_schema={"ok": bool},
            capabilities=frozenset({Capability.FILESYSTEM_READ}),
            risk_level=ToolRiskLevel.READ_ONLY,
            timeout_seconds=0.05,
        ),
        _slow,
    )
    reg.register(
        ToolSpec(
            tool_id="sys.huge",
            input_schema={},
            output_schema={"content": str},
            capabilities=frozenset({Capability.FILESYSTEM_READ}),
            risk_level=ToolRiskLevel.READ_ONLY,
            max_output_bytes=128,
        ),
        _huge,
    )
    reg.register(
        ToolSpec(
            tool_id="net.fetch",
            input_schema={"url": str},
            output_schema={"body": str},
            capabilities=frozenset({Capability.NETWORK}),
            risk_level=ToolRiskLevel.READ_ONLY,
        ),
        _read,
    )

    # 1. Read-only permitted tool succeeds and audits
    res = await sandbox.execute(ToolCall(tool_id="fs.read", arguments={"path": "notes.txt"}))
    assert res.success is True
    assert res.output == {"content": "notes.txt"}

    # 2. Missing capability rejected before execution
    with pytest.raises(ToolPermissionDeniedError):
        await sandbox.execute(ToolCall(tool_id="net.fetch", arguments={"url": "https://x"}))

    # 3. Destructive tool default-deny (PermissionPolicy denies DANGEROUS unless granted)
    with pytest.raises(ToolPermissionDeniedError):
        await sandbox.execute(ToolCall(tool_id="fs.delete", arguments={"path": "a.txt"}))

    # Even if granted in policy, denied approval blocks execution
    sandbox._policy.granted_tools.add("fs.delete")  # noqa: SLF001
    with pytest.raises(ApprovalDeniedError):
        await sandbox.execute(ToolCall(tool_id="fs.delete", arguments={"path": "a.txt"}))
    assert not any(item.startswith("delete:") for item in executed)

    # 4. Timeout & output limit enforced
    with pytest.raises(ToolExecutionError, match="timed out"):
        await sandbox.execute(ToolCall(tool_id="sys.slow", arguments={}))
    with pytest.raises(ToolOutputLimitError):
        await sandbox.execute(ToolCall(tool_id="sys.huge", arguments={}))

    # 5. Kill switch blocks execution
    await lifecycle.trigger_kill_switch("test")
    with pytest.raises(KillSwitchActiveError):
        await sandbox.execute(ToolCall(tool_id="fs.read", arguments={"path": "notes.txt"}))

    assert len(audit.read_all()) >= 6
