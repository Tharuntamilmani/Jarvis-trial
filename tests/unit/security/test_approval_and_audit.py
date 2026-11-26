"""Unit tests for Phase 7.2 payload-bound ApprovalService, AuditService & auto-kill-switch."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jarvis.config import Settings
from jarvis.core.event_bus import EventBus
from jarvis.core.exceptions import ApprovalDeniedError
from jarvis.core.lifecycle import LifecycleManager
from jarvis.core.service_registry import ServiceRegistry
from jarvis.security.allowlist import CommandAllowlist
from jarvis.security.approval import ApprovalService
from jarvis.security.audit import AuditPersistenceError, AuditService, redact_audit_detail
from jarvis.security.permissions import Capability, PermissionPolicy
from jarvis.security.sandbox import ExecutionSandbox
from jarvis.tools import ToolCall, ToolRegistry, ToolRiskLevel, ToolSpec


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        log_dir=tmp_path / "logs",
        audit_log_path=tmp_path / "logs" / "audit.log",
        approval_timeout_seconds=0.3,
    )


def test_audit_redaction_strips_sensitive_fields() -> None:
    redacted = redact_audit_detail(
        {
            "path": "notes.txt",
            "api_key": "sk-secret",
            "clipboard": "private text",
            "screenshot": b"PNG...",
            "embedding": [0.1, 0.2],
        }
    )
    assert redacted["path"] == "notes.txt"
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["clipboard"] == "[REDACTED]"
    assert redacted["screenshot"] == "[REDACTED]"
    assert redacted["embedding"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_approval_bound_to_exact_payload_single_use_and_kill_switch(
    tmp_path: Path,
) -> None:
    bus = EventBus()
    settings = _settings(tmp_path)
    lifecycle = LifecycleManager(ServiceRegistry(), bus)
    approval = ApprovalService(settings, bus, lifecycle=lifecycle)
    approval.add_responder(lambda _ev: asyncio.sleep(0, result=True))

    call_a = ToolCall(tool_id="fs.move", arguments={"src": "a.txt", "dst": "b.txt"})
    fp_a = call_a.canonical_fingerprint("1.0.0")
    req_id = await approval.request(
        "fs.move",
        "Move a.txt -> b.txt",
        fingerprint=fp_a,
        tool_id="fs.move",
        risk_level="destructive",
    )

    # Modified arguments produce a different fingerprint -> rejected!
    call_modified = ToolCall(tool_id="fs.move", arguments={"src": "a.txt", "dst": "evil.txt"})
    assert approval.verify_and_consume(req_id, call_modified.canonical_fingerprint("1.0.0")) is False

    # Exact fingerprint succeeds once, then replay is rejected!
    assert approval.verify_and_consume(req_id, fp_a) is True
    assert approval.verify_and_consume(req_id, fp_a) is False

    # Kill switch invalidates unused approval
    req_id2 = await approval.request("fs.move", "Move again", fingerprint=fp_a)
    await lifecycle.trigger_kill_switch("emergency")
    assert approval.verify_and_consume(req_id2, fp_a) is False


@pytest.mark.asyncio
async def test_sandbox_blocks_consequential_exec_on_audit_failure_and_auto_kills(
    tmp_path: Path,
) -> None:
    bus = EventBus()
    settings = _settings(tmp_path)
    lifecycle = LifecycleManager(ServiceRegistry(), bus)
    audit = AuditService(settings, bus)
    approval = ApprovalService(settings, bus, lifecycle=lifecycle)
    approval.add_responder(lambda _ev: asyncio.sleep(0, result=True))

    reg = ToolRegistry()
    executed: list[str] = []

    async def _write(args, ctx):
        del ctx
        executed.append(args["path"])
        return {"ok": True}

    reg.register(
        ToolSpec(
            tool_id="fs.write",
            input_schema={"path": str},
            output_schema={"ok": bool},
            capabilities=frozenset({Capability.FILESYSTEM_WRITE}),
            risk_level=ToolRiskLevel.STATE_CHANGING,
        ),
        _write,
    )

    sandbox = ExecutionSandbox(
        registry=reg,
        policy=PermissionPolicy(granted_capabilities={Capability.FILESYSTEM_WRITE}),
        allowlist=CommandAllowlist(),
        approval=approval,
        audit=audit,
        lifecycle=lifecycle,
        require_db_audit=True,  # No DB attached -> consequential audit fails closed!
        auto_kill_switch_threshold=2,
    )

    with pytest.raises(AuditPersistenceError):
        await sandbox.execute(ToolCall(tool_id="fs.write", arguments={"path": "x.txt"}))
    assert executed == []
    assert lifecycle.kill_switch_active is False

    # 2nd consecutive audit failure trips auto-kill-switch
    with pytest.raises(AuditPersistenceError):
        await sandbox.execute(ToolCall(tool_id="fs.write", arguments={"path": "x.txt"}))
    assert lifecycle.kill_switch_active is True

    # Ordinary user denial does not falsely count as infrastructure failure
    lifecycle2 = LifecycleManager(ServiceRegistry(), bus)
    approval2 = ApprovalService(settings, bus, lifecycle=lifecycle2)
    approval2.add_responder(lambda _ev: asyncio.sleep(0, result=False))
    sandbox2 = ExecutionSandbox(
        registry=reg,
        policy=PermissionPolicy(granted_capabilities={Capability.FILESYSTEM_WRITE}),
        allowlist=CommandAllowlist(),
        approval=approval2,
        audit=audit,
        lifecycle=lifecycle2,
        auto_kill_switch_threshold=2,
    )
    for _ in range(3):
        with pytest.raises(ApprovalDeniedError):
            await sandbox2.execute(ToolCall(tool_id="fs.write", arguments={"path": "x.txt"}))
    assert lifecycle2.kill_switch_active is False
