"""UI tests for Phase 7.4 Workspace SecurityView & HUD live telemetry."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication

from jarvis.config import Settings
from jarvis.core.event_bus import EventBus
from jarvis.core.events import TelemetryUpdated
from jarvis.core.lifecycle import LifecycleManager
from jarvis.core.service_registry import ServiceRegistry
from jarvis.desktop.adapters.event_bridge import EventBridge
from jarvis.desktop.viewmodels.app_state import AppState
from jarvis.desktop.viewmodels.security_controller import SecurityController
from jarvis.desktop.views.security_view import SecurityView
from jarvis.desktop.windows.hud_window import HudWindow
from jarvis.desktop.windows.workspace_window import WorkspaceWindow
from jarvis.security.allowlist import CommandAllowlist
from jarvis.security.audit import AuditService
from jarvis.security.permissions import Capability, PermissionPolicy
from jarvis.tools import ToolRegistry, ToolRiskLevel, ToolSpec


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_security_view_permissions_allowlist_audit_and_kill_switch(
    tmp_path: Path,
) -> None:
    _qapp()
    bus = EventBus()
    state = AppState()
    EventBridge(bus, state)
    settings = Settings(
        log_dir=tmp_path / "logs",
        audit_log_path=tmp_path / "logs" / "audit.log",
    )
    lifecycle = LifecycleManager(ServiceRegistry(), bus)
    policy = PermissionPolicy(granted_capabilities={Capability.FILESYSTEM_READ})
    allowlist = CommandAllowlist()
    allowlist.add_exact("notepad")
    audit = AuditService(settings, bus)
    audit.record("fs.read", result="completed", detail={"correlation_id": "c-1"})
    reg = ToolRegistry()

    async def _noop(args, ctx):
        del args, ctx
        return {}

    reg.register(
        ToolSpec(
            tool_id="fs.read",
            input_schema={},
            capabilities=frozenset({Capability.FILESYSTEM_READ}),
            risk_level=ToolRiskLevel.READ_ONLY,
        ),
        _noop,
    )

    ctrl = SecurityController(
        state=state,
        policy=policy,
        allowlist=allowlist,
        audit=audit,
        registry=reg,
        lifecycle=lifecycle,
    )
    view = SecurityView(state, ctrl)

    assert view.tools_list.count() == 1
    assert view.allowlist_widget.count() == 1
    assert view.audit_list.count() == 1

    # Add valid allowlist command; reject empty/shell-metacharacter input
    assert ctrl.add_allowlist_command("calc") is True
    assert allowlist.is_allowed("calc") is True
    assert ctrl.add_allowlist_command("cmd && whoami") is False

    # Toggle capability through controller
    ctrl.set_capability_enabled(Capability.CLIPBOARD, True)
    assert Capability.CLIPBOARD in policy.granted_capabilities

    # Kill switch status reflects state transitions
    state.set_kill_switch(True)
    assert "ENGAGED" in view.kill_switch_label.text()
    state.set_kill_switch(False)
    assert "ARMED" in view.kill_switch_label.text()


def test_hud_and_workspace_integrate_telemetry_and_security_tab() -> None:
    _qapp()
    bus = EventBus()
    state = AppState()
    bridge = EventBridge(bus, state)

    hud = HudWindow(state)
    ws = WorkspaceWindow(state)
    assert ws.security_view is not None

    bridge._on_telemetry(  # noqa: SLF001
        TelemetryUpdated(
            cpu_percent=37.5,
            ram_percent=62.0,
            disk_percent=48.0,
            gpu_percent=21.0,
        )
    )
    assert "37.5%" in hud.telemetry_label.text()
    assert "62.0%" in hud.telemetry_label.text()
    assert "48.0%" in hud.telemetry_label.text()
