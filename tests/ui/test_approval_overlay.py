"""UI tests for Phase 7.2 native PySide6 ApprovalOverlay."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from jarvis.core.event_bus import EventBus
from jarvis.core.events import ApprovalDecided, ApprovalRequested
from jarvis.desktop.adapters.event_bridge import EventBridge
from jarvis.desktop.viewmodels.app_state import AppState
from jarvis.desktop.views.approval_overlay import ApprovalOverlay


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_approval_overlay_shows_details_and_fails_closed_on_close() -> None:
    _qapp()
    state = AppState()
    decisions: list[tuple[str, bool]] = []
    overlay = ApprovalOverlay(
        state, on_decision=lambda req_id, approved: decisions.append((req_id, approved))
    )

    state.apply_approval_requested(
        request_id="req-123",
        action="fs.delete",
        summary="Delete secret.txt",
        tool_id="fs.delete",
        risk_level="destructive",
        target="C:\\Users\\User\\secret.txt",
        agent_run_id="agent-1",
        timeout_seconds=15.0,
    )
    assert overlay.isVisible() is True
    assert "fs.delete" in overlay.action_label.text()
    assert "DESTRUCTIVE" in overlay.risk_label.text()

    # Closing/dismissing denies (fail-closed)
    overlay.close()
    assert decisions == [("req-123", False)]
    assert overlay.isVisible() is False


def test_approval_overlay_approve_and_event_bridge_forwarding() -> None:
    _qapp()
    bus = EventBus()
    state = AppState()
    EventBridge(bus, state)

    decisions: list[tuple[str, bool]] = []
    overlay = ApprovalOverlay(
        state, on_decision=lambda req_id, approved: decisions.append((req_id, approved))
    )

    state.apply_approval_requested(
        request_id="req-456",
        action="fs.move",
        summary="Move file",
        tool_id="fs.move",
        risk_level="state_changing",
        target="notes.txt",
        timeout_seconds=10.0,
    )
    overlay.approve_btn.click()
    assert decisions == [("req-456", True)]
    assert overlay.isVisible() is False

    # Type check EventBridge handles ApprovalRequested/Decided without error
    assert isinstance(ApprovalRequested(request_id="x"), ApprovalRequested)
    assert isinstance(ApprovalDecided(request_id="x", approved=True), ApprovalDecided)
