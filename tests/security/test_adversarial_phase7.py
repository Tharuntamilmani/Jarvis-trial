"""Adversarial security & hardening tests for Phase 7.5."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from jarvis.config import Settings
from jarvis.core.event_bus import EventBus
from jarvis.core.exceptions import KillSwitchActiveError
from jarvis.core.lifecycle import LifecycleManager
from jarvis.core.service_registry import ServiceRegistry
from jarvis.intelligence.cloud_payload import build_cloud_payload, serialize_cloud_body
from jarvis.intelligence.prompts import JARVIS_SYSTEM_PROMPT
from jarvis.intelligence.providers.base import ChatMessage, ChatRequest
from jarvis.security.allowlist import CommandAllowlist
from jarvis.security.approval import ApprovalService
from jarvis.security.audit import AuditPersistenceError, AuditService
from jarvis.security.permissions import Capability, PermissionPolicy
from jarvis.security.sandbox import ExecutionSandbox
from jarvis.tools import (
    AllowedApp,
    FilesystemConfig,
    SystemToolsConfig,
    ToolCall,
    ToolExecutionError,
    ToolPermissionDeniedError,
    ToolRegistrationError,
    ToolRegistry,
    ToolRiskLevel,
    ToolSpec,
    register_clipboard_tools,
    register_filesystem_tools,
    register_system_tools,
    register_telemetry_tools,
)


def _setup(
    tmp_path: Path,
    *,
    caps: set[Capability] | None = None,
    approve: bool = True,
    require_db_audit: bool = False,
):
    bus = EventBus()
    settings = Settings(
        log_dir=tmp_path / "logs",
        audit_log_path=tmp_path / "logs" / "audit.log",
        approval_timeout_seconds=0.3,
    )
    lifecycle = LifecycleManager(ServiceRegistry(), bus)
    audit = AuditService(settings, bus)
    approval = ApprovalService(settings, bus, lifecycle=lifecycle, grant_ttl_seconds=1.0)
    approval.add_responder(lambda _ev: asyncio.sleep(0, result=approve))
    policy = PermissionPolicy(granted_capabilities=caps or set())
    allowlist = CommandAllowlist()
    allowlist.add_exact("notepad")
    reg = ToolRegistry()
    sb = ExecutionSandbox(
        registry=reg,
        policy=policy,
        allowlist=allowlist,
        approval=approval,
        audit=audit,
        lifecycle=lifecycle,
        require_db_audit=require_db_audit,
        auto_kill_switch_threshold=2,
    )
    return sb, reg, policy, approval, audit, lifecycle


@pytest.mark.asyncio
async def test_adversarial_authorization_and_approval_replay_blocked(
    tmp_path: Path,
) -> None:
    sb, reg, policy, approval, _, lifecycle = _setup(
        tmp_path, caps={Capability.FILESYSTEM_READ}, approve=True
    )

    async def _noop(args, ctx):
        del args, ctx
        return {"ok": True}

    # Agent cannot register or unregister tools
    spec = ToolSpec(
        tool_id="admin.exec",
        input_schema={"cmd": str},
        output_schema={"ok": bool},
        capabilities=frozenset({Capability.PROCESS}),
        risk_level=ToolRiskLevel.DESTRUCTIVE,
    )
    with pytest.raises(ToolRegistrationError):
        reg.register(spec, _noop, caller_is_agent=True)

    # Unregistered tool call denied
    with pytest.raises(ToolExecutionError):
        await sb.execute(ToolCall(tool_id="unknown.tool", arguments={}))

    # Destructive tool default-deny even with capability
    reg.register(spec, _noop)
    policy.granted_capabilities.add(Capability.PROCESS)
    with pytest.raises(ToolPermissionDeniedError):
        await sb.execute(ToolCall(tool_id="admin.exec", arguments={"cmd": "whoami"}))

    # Approval integrity: modified payload & replay both fail
    c1 = ToolCall(tool_id="admin.exec", arguments={"cmd": "notepad"})
    req_id = await approval.request("admin.exec", fingerprint=c1.canonical_fingerprint("1.0.0"))
    c2 = ToolCall(tool_id="admin.exec", arguments={"cmd": "powershell"})
    assert approval.verify_and_consume(req_id, c2.canonical_fingerprint("1.0.0")) is False
    assert approval.verify_and_consume(req_id, c1.canonical_fingerprint("1.0.0")) is True
    assert approval.verify_and_consume(req_id, c1.canonical_fingerprint("1.0.0")) is False

    # Kill switch blocks all execution
    await lifecycle.trigger_kill_switch("adversarial")
    with pytest.raises(KillSwitchActiveError):
        await sb.execute(ToolCall(tool_id="admin.exec", arguments={"cmd": "notepad"}))


@pytest.mark.asyncio
async def test_adversarial_filesystem_escape_and_no_shell_true(
    tmp_path: Path,
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("SECRET_OUTSIDE_FILE", encoding="utf-8")

    sb, reg, _, _, _, _ = _setup(
        tmp_path, caps={Capability.FILESYSTEM_READ, Capability.PROCESS}, approve=True
    )
    register_filesystem_tools(reg, FilesystemConfig(allowed_roots=(ws,), max_read_bytes=32))
    register_system_tools(
        reg,
        SystemToolsConfig(
            allowed_apps={
                "notepad": AllowedApp("notepad", Path("C:/Windows/System32/notepad.exe"))
            }
        ),
    )

    for bad_path in (
        "../secret.txt",
        str(outside),
        "\\\\127.0.0.1\\c$\\secret.txt",
        "bad\x00path.txt",
    ):
        with pytest.raises(ToolPermissionDeniedError):
            await sb.execute(ToolCall(tool_id="fs.read", arguments={"path": bad_path}))

    # Oversized file rejected
    big = ws / "big.txt"
    big.write_text("A" * 128, encoding="utf-8")
    with pytest.raises(ToolExecutionError, match="max_read_bytes"):
        await sb.execute(ToolCall(tool_id="fs.read", arguments={"path": str(big)}))

    # Symlink escape rejected
    link = ws / "sym.txt"
    try:
        os.symlink(outside, link)
    except OSError:
        pass
    else:
        with pytest.raises(ToolPermissionDeniedError):
            await sb.execute(ToolCall(tool_id="fs.read", arguments={"path": str(link)}))

    # Static AST check: no shell=True keyword argument anywhere in tools/ or security/
    import ast

    src_root = Path(__file__).resolve().parents[2] / "src" / "jarvis"
    for folder in ("tools", "security"):
        for py in (src_root / folder).glob("*.py"):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    for kw in node.keywords:
                        assert not (
                            kw.arg == "shell"
                            and isinstance(kw.value, ast.Constant)
                            and kw.value.value is True
                        ), f"shell=True found in {py}"


@pytest.mark.asyncio
async def test_adversarial_audit_immutability_and_cloud_isolation(
    tmp_path: Path,
) -> None:
    sb, reg, policy, _, audit, _ = _setup(
        tmp_path,
        caps={Capability.FILESYSTEM_WRITE, Capability.CLIPBOARD},
        approve=True,
        require_db_audit=True,
    )
    register_filesystem_tools(reg, FilesystemConfig(allowed_roots=(tmp_path,)))
    policy.granted_tools.add("fs.mkdir")

    # AuditService exposes zero update/delete methods
    assert not hasattr(audit, "delete")
    assert not hasattr(audit, "update")

    # Consequential call blocked when authoritative DB audit is unavailable
    with pytest.raises(AuditPersistenceError):
        await sb.execute(
            ToolCall(tool_id="fs.mkdir", arguments={"path": str(tmp_path / "newdir")})
        )
    assert not (tmp_path / "newdir").exists()

    # CloudPayload isolation: local clipboard/telemetry/file data never enters CloudPayload
    class _FakeClip:
        def read_text(self) -> str:
            return "CLIPBOARD_SECRET_XYZ"

        def write_text(self, text: str) -> None:
            del text

        def clear(self) -> None:
            return None

    register_clipboard_tools(reg, backend=_FakeClip())
    register_telemetry_tools(reg)
    req = ChatRequest(
        messages=(ChatMessage(role="user", content="hello jarvis"),),
        model="llama-3.1-8b-instant",
        request_id="req-adv-1",
    )
    body = json.dumps(
        serialize_cloud_body(
            build_cloud_payload(req, system_prompt=JARVIS_SYSTEM_PROMPT),
            default_model="llama-3.1-8b-instant",
        )
    )
    assert "CLIPBOARD_SECRET_XYZ" not in body
