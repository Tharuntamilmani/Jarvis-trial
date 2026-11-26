"""Unit & security tests for Phase 7.3 Core Windows Tools."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from jarvis.config import Settings
from jarvis.core.event_bus import EventBus
from jarvis.core.exceptions import ApprovalDeniedError
from jarvis.core.lifecycle import LifecycleManager
from jarvis.core.service_registry import ServiceRegistry
from jarvis.security.allowlist import CommandAllowlist
from jarvis.security.approval import ApprovalService
from jarvis.security.audit import AuditService
from jarvis.security.permissions import Capability, PermissionPolicy
from jarvis.security.sandbox import ExecutionSandbox
from jarvis.tools import ToolCall, ToolPermissionDeniedError, ToolRegistry
from jarvis.tools.clipboard import register_clipboard_tools
from jarvis.tools.filesystem import FilesystemConfig, register_filesystem_tools, resolve_safe_path
from jarvis.tools.system import AllowedApp, SystemToolsConfig, register_system_tools
from jarvis.tools.telemetry import TelemetryCollector, register_telemetry_tools


class _FakeClipboard:
    def __init__(self, initial: str = "secret clipboard") -> None:
        self.text = initial

    def read_text(self) -> str:
        return self.text

    def write_text(self, text: str) -> None:
        self.text = text

    def clear(self) -> None:
        self.text = ""


def _make_sandbox(
    tmp_path: Path,
    *,
    caps: set[Capability],
    allowlist: CommandAllowlist | None = None,
    approve: bool = True,
) -> tuple[ExecutionSandbox, ToolRegistry]:
    bus = EventBus()
    settings = Settings(
        log_dir=tmp_path / "logs",
        audit_log_path=tmp_path / "logs" / "audit.log",
        approval_timeout_seconds=0.5,
    )
    lifecycle = LifecycleManager(ServiceRegistry(), bus)
    audit = AuditService(settings, bus)
    approval = ApprovalService(settings, bus, lifecycle=lifecycle)
    approval.add_responder(lambda _ev: asyncio.sleep(0, result=approve))
    reg = ToolRegistry()
    sb = ExecutionSandbox(
        registry=reg,
        policy=PermissionPolicy(granted_capabilities=caps),
        allowlist=allowlist or CommandAllowlist(),
        approval=approval,
        audit=audit,
        lifecycle=lifecycle,
    )
    return sb, reg


def test_filesystem_path_traversal_unc_and_symlink_escape_rejected(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("top secret", encoding="utf-8")

    # Traversal & UNC rejected
    with pytest.raises(ToolPermissionDeniedError):
        resolve_safe_path("../outside.txt", (root,))
    with pytest.raises(ToolPermissionDeniedError):
        resolve_safe_path("\\\\server\\share\\file.txt", (root,))
    with pytest.raises(ToolPermissionDeniedError):
        resolve_safe_path(str(outside), (root,))

    # Symlink / junction escape rejected when supported
    link = root / "escape_link.txt"
    try:
        os.symlink(outside, link)
    except OSError:
        pass
    else:
        with pytest.raises(ToolPermissionDeniedError):
            resolve_safe_path(str(link), (root,))


@pytest.mark.asyncio
async def test_filesystem_tools_through_sandbox(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    sample = root / "hello.txt"
    sample.write_text("hello jarvis", encoding="utf-8")

    sb, reg = _make_sandbox(
        tmp_path,
        caps={Capability.FILESYSTEM_READ, Capability.FILESYSTEM_WRITE},
        approve=True,
    )
    register_filesystem_tools(reg, FilesystemConfig(allowed_roots=(root,)))

    res_read = await sb.execute(
        ToolCall(tool_id="fs.read", arguments={"path": str(sample)})
    )
    assert res_read.output["content"] == "hello jarvis"

    res_list = await sb.execute(
        ToolCall(tool_id="fs.list", arguments={"path": str(root)})
    )
    assert "hello.txt" in res_list.output["entries"]

    # Destructive fs.delete requires policy grant + approval
    sb._policy.granted_tools.add("fs.delete")  # noqa: SLF001
    res_del = await sb.execute(
        ToolCall(tool_id="fs.delete", arguments={"path": str(sample)})
    )
    assert res_del.output["deleted"] is True
    assert not sample.exists()


@pytest.mark.asyncio
async def test_system_launcher_clipboard_and_telemetry_tools(tmp_path: Path) -> None:
    allowlist = CommandAllowlist()
    allowlist.add_exact("notepad")
    sb, reg = _make_sandbox(
        tmp_path,
        caps={Capability.PROCESS, Capability.CLIPBOARD, Capability.SYSTEM_SETTINGS},
        allowlist=allowlist,
        approve=True,
    )

    launched: list[tuple[str, list[str]]] = []

    async def _fake_launcher(exe: Path, args: list[str]) -> int:
        launched.append((str(exe), args))
        return 4242

    register_system_tools(
        reg,
        SystemToolsConfig(
            allowed_apps={
                "notepad": AllowedApp(
                    app_id="notepad",
                    executable=Path("C:/Windows/System32/notepad.exe"),
                    allowed_args=frozenset(),
                )
            }
        ),
        launcher=_fake_launcher,
        screenshot_provider=lambda: b"FAKE_PNG_BYTES",
        ocr_provider=lambda raw: f"ocr:{len(raw)}",
    )
    clip = _FakeClipboard("private")
    register_clipboard_tools(reg, backend=clip)
    register_telemetry_tools(reg, TelemetryCollector(min_interval_seconds=0.0))

    # Launch allowlisted app without flags
    res_app = await sb.execute(
        ToolCall(tool_id="sys.launch_app", arguments={"app_id": "notepad", "args": []})
    )
    assert res_app.output["pid"] == 4242
    assert len(launched) == 1

    # Unallowlisted / unapproved flag rejected
    with pytest.raises(ToolPermissionDeniedError):
        await sb.execute(
            ToolCall(
                tool_id="sys.launch_app",
                arguments={"app_id": "notepad", "args": ["--evil"]},
            )
        )

    # Screenshot OCR runs in-memory without writing to disk
    res_ocr = await sb.execute(ToolCall(tool_id="sys.screenshot_ocr", arguments={}))
    assert res_ocr.output["text"] == "ocr:14"

    # Clipboard read/write
    res_clip = await sb.execute(ToolCall(tool_id="clipboard.read", arguments={}))
    assert res_clip.output["text"] == "private"

    # Telemetry snapshot
    res_tel = await sb.execute(ToolCall(tool_id="sys.telemetry", arguments={}))
    assert "cpu_percent" in res_tel.output
    assert "ram_percent" in res_tel.output
    assert "disk_percent" in res_tel.output

    # Denied approval blocks clipboard write
    sb2, reg2 = _make_sandbox(tmp_path, caps={Capability.CLIPBOARD}, approve=False)
    register_clipboard_tools(reg2, backend=clip)
    with pytest.raises(ApprovalDeniedError):
        await sb2.execute(
            ToolCall(tool_id="clipboard.write", arguments={"text": "overwrite"})
        )
    assert clip.text == "private"
