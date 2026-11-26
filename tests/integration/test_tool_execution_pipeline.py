"""End-to-end integration test for Phase 7 tool execution + DB audit (jarvis_test)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jarvis.config import Settings
from jarvis.core.event_bus import EventBus
from jarvis.core.lifecycle import LifecycleManager
from jarvis.core.service_registry import ServiceRegistry
from jarvis.security.allowlist import CommandAllowlist
from jarvis.security.approval import ApprovalService
from jarvis.security.audit import AuditService
from jarvis.security.permissions import Capability, PermissionPolicy
from jarvis.security.sandbox import ExecutionSandbox
from jarvis.tools import (
    FilesystemConfig,
    ToolCall,
    ToolRegistry,
    register_filesystem_tools,
)

pytestmark = pytest.mark.usefixtures("migrated_database")


async def test_end_to_end_tool_execution_persists_correlated_audit_in_jarvis_test(
    db_service, tmp_path: Path
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    settings = Settings(
        log_dir=tmp_path / "logs",
        audit_log_path=tmp_path / "logs" / "audit.log",
        approval_timeout_seconds=1.0,
    )
    bus = EventBus()
    lifecycle = LifecycleManager(ServiceRegistry(), bus)
    audit = AuditService(settings, bus, database=db_service)
    approval = ApprovalService(settings, bus, lifecycle=lifecycle)
    approval.add_responder(lambda _ev: asyncio.sleep(0, result=True))
    reg = ToolRegistry()
    register_filesystem_tools(reg, FilesystemConfig(allowed_roots=(ws,)))

    sb = ExecutionSandbox(
        registry=reg,
        policy=PermissionPolicy(
            granted_capabilities={Capability.FILESYSTEM_READ, Capability.FILESYSTEM_WRITE}
        ),
        allowlist=CommandAllowlist(),
        approval=approval,
        audit=audit,
        lifecycle=lifecycle,
        require_db_audit=True,
    )

    call = ToolCall(
        tool_id="fs.mkdir",
        arguments={"path": str(ws / "project_docs")},
        correlation_id="corr-e2e-75",
        agent_run_id="agent-e2e",
    )
    res = await sb.execute(call)
    assert res.success is True
    assert (ws / "project_docs").is_dir()

    rows = await audit.query_db(correlation_id="corr-e2e-75")
    results = {r["result"] for r in rows}
    assert "started" in results
    assert "completed" in results
