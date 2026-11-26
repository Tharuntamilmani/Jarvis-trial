"""UI smoke tests for the three windows and window lifecycle semantics.

Uses pytest-qt with the real (non-qasync) Qt loop — the windows only bind
to the shared AppState, so no core engine is required here.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from jarvis.desktop.viewmodels.app_state import AppState
from jarvis.desktop.viewmodels.chat_controller import ChatController
from jarvis.desktop.window_manager import Mode, WindowManager
from jarvis.desktop.windows.floating_window import FloatingWindow
from jarvis.desktop.windows.hud_window import HudWindow
from jarvis.desktop.windows.workspace_window import WorkspaceWindow
from tests.ui.chat_test_helpers import StubConversationPersistence


@pytest.fixture()
def state(qtbot) -> AppState:
    return AppState()


def test_hud_window_renders_and_reacts(qtbot, state) -> None:
    hud = HudWindow(state)
    qtbot.addWidget(hud)
    state.apply_service_status("database", "healthy", "connected")
    assert hud._chips["database"].current_status == "connected"
    state.set_assistant_state("thinking")
    assert hud.orb.state == "thinking"


def test_workspace_window_renders_and_reacts(qtbot, state) -> None:
    ws = WorkspaceWindow(state)
    qtbot.addWidget(ws)
    state.apply_service_status("engine", "running", "started")
    assert ws._chips["engine"].current_status == "started"
    ws.nav.setCurrentRow(5)  # Status page
    assert ws.content.currentIndex() == 5


def test_floating_window_renders_and_reacts(qtbot, state) -> None:
    floating = FloatingWindow(state)
    qtbot.addWidget(floating)
    state.set_assistant_state("listening")
    assert floating.orb.state == "listening"
    floating.command_input.setText("open notepad")
    floating.command_input.returnPressed.emit()
    floating.command_input.setText("hello")


def test_shared_state_single_instance_updates_all_windows(
    qtbot, state
) -> None:
    hud = HudWindow(state)
    ws = WorkspaceWindow(state)
    floating = FloatingWindow(state)
    for w in (hud, ws, floating):
        qtbot.addWidget(w)
    # One event reaches all three windows through the same AppState.
    state.apply_service_status("providers", "degraded", "stub")
    assert hud._chips["providers"].current_status == "stub"
    assert ws._chips["providers"].current_status == "stub"


# -- Window manager lifecycle semantics -----------------------------------------


def test_mode_switch_is_pure_visibility(qtbot, state) -> None:
    wm = WindowManager(state)
    wm.switch_mode(Mode.WORKSPACE)
    assert wm.is_visible(Mode.WORKSPACE)
    wm.switch_mode(Mode.HUD)
    assert wm.is_visible(Mode.HUD)
    assert not wm.is_visible(Mode.WORKSPACE)
    # Window objects still exist (state preserved, hidden not destroyed).
    assert wm.is_open(Mode.WORKSPACE)


def test_hide_preserves_object(qtbot, state) -> None:
    wm = WindowManager(state)
    wm.show_window(Mode.FLOATING)
    assert wm.is_visible(Mode.FLOATING)
    wm.hide_window(Mode.FLOATING)
    assert not wm.is_visible(Mode.FLOATING)
    assert wm.is_open(Mode.FLOATING)  # object alive, restorable


def test_close_destroys_only_that_window(qtbot, state) -> None:
    wm = WindowManager(state)
    wm.show_window(Mode.WORKSPACE)
    wm.show_window(Mode.FLOATING)
    wm.close_window(Mode.WORKSPACE)
    assert not wm.is_open(Mode.WORKSPACE)
    assert wm.is_open(Mode.FLOATING)  # unaffected
    # Engine proxy check: closing a window must not touch core state.
    assert state.engine_state == "stopped"  # engine state untouched by UI ops


def test_tray_quit_path_emits_graceful_shutdown_request(qtbot, state) -> None:
    wm = WindowManager(state)
    requests: list[bool] = []
    wm.shutdown_requested.connect(lambda: requests.append(True))
    wm.request_shutdown()
    assert requests == [True]


def test_shutdown_closes_everything(qtbot, state) -> None:
    wm = WindowManager(state)
    wm.switch_mode(Mode.HUD)
    wm.switch_mode(Mode.WORKSPACE)  # HUD hidden, workspace shown
    wm.shutdown()
    assert wm.open_keys == ()
    assert state.engine_state == "stopped"


# -- Status integration (Stage 2.7) -------------------------------------------------


def test_status_chips_hydrate_from_shared_state(qtbot, state) -> None:
    state.apply_service_status("database", "healthy", "connected")
    state.set_engine_state("running")
    state.apply_service_status("kill_switch", "healthy", "armed")

    hud = HudWindow(state)
    ws = WorkspaceWindow(state)
    for window in (hud, ws):
        qtbot.addWidget(window)

    assert hud._chips["database"].current_status == "connected"  # noqa: SLF001
    assert hud._chips["engine"].current_status == "running"  # noqa: SLF001
    assert hud._chips["kill_switch"].current_status == "armed"  # noqa: SLF001
    assert ws._chips["engine"].current_status == "running"  # noqa: SLF001


def test_engine_status_mapping_is_applied_to_shared_state(state) -> None:
    state.set_engine_state("running")
    assert state.service_statuses["engine"] == "healthy"
    assert state.service_details["engine"] == "running"

    state.set_engine_state("kill_switch")
    assert state.service_statuses["engine"] == "degraded"


def test_provider_chip_aggregates_availability(state) -> None:
    state.apply_provider_health("ollama", True, "connected")
    assert state.service_statuses["providers"] == "healthy"
    assert state.service_details["providers"] == "1/1 available"

    state.apply_provider_health("groq", False, "not configured")
    assert state.service_statuses["providers"] == "healthy"
    assert state.service_details["providers"] == "1/2 available"

    state.apply_provider_health("ollama", False, "offline")
    assert state.service_statuses["providers"] == "unavailable"
    assert state.service_details["providers"] == "0/2 available"


def test_hud_kill_switch_restores_on_rearm(qtbot, state) -> None:
    state.set_assistant_state("thinking")
    hud = HudWindow(state)
    qtbot.addWidget(hud)
    assert hud.orb.state == "thinking"

    state.set_kill_switch(True)
    assert hud.orb.state == "kill_switch"
    assert hud.assistant_label.text() == "EMERGENCY STOP ENGAGED"

    # Assistant-state events must not replace the emergency indicator.
    state.set_assistant_state("speaking")
    assert hud.orb.state == "kill_switch"
    assert hud.assistant_label.text() == "EMERGENCY STOP ENGAGED"

    state.set_kill_switch(False)
    assert hud.orb.state == "speaking"
    assert hud.assistant_label.text() == "SPEAKING"


def test_floating_orb_reflects_kill_switch(qtbot, state) -> None:
    state.set_assistant_state("thinking")
    floating = FloatingWindow(state, command_handler=lambda _text: True)
    qtbot.addWidget(floating)
    assert floating.orb.state == "thinking"

    state.set_kill_switch(True)
    assert floating.orb.state == "kill_switch"
    assert floating.state_label.text() == "emergency stop"
    assert not floating.command_input.isEnabled()

    state.set_kill_switch(False)
    assert floating.orb.state == "thinking"
    assert floating.command_input.isEnabled()


def test_hud_f_requests_floating_mode(qtbot, state) -> None:
    hud = HudWindow(state)
    qtbot.addWidget(hud)
    fired: list[bool] = []
    hud.switch_floating_requested.connect(lambda: fired.append(True))

    QTest.keyClick(hud, Qt.Key.Key_F)

    assert fired == [True]


def test_window_manager_hud_f_switches_to_floating(qtbot, state) -> None:
    wm = WindowManager(state)
    wm.switch_mode(Mode.HUD)
    wm.window(Mode.HUD).switch_floating_requested.emit()  # noqa: SLF001
    assert wm.is_visible(Mode.FLOATING)


def test_floating_without_handler_is_clearly_disabled(qtbot, state) -> None:
    floating = FloatingWindow(state)
    qtbot.addWidget(floating)
    assert not floating.command_input.isEnabled()
    assert "unavailable" in floating.command_input.placeholderText().lower()


def test_rejected_quick_command_keeps_text(qtbot, state) -> None:
    seen: list[str] = []

    def reject(text: str) -> bool:
        seen.append(text)
        return False

    floating = FloatingWindow(state, command_handler=reject)
    qtbot.addWidget(floating)
    floating.command_input.setText("hello")
    floating.command_input.returnPressed.emit()

    assert seen == ["hello"]
    assert floating.command_input.text() == "hello"
    assert floating.state_label.text() == "not sent"


async def test_floating_quick_command_routes_through_chat(qtbot, state) -> None:
    class _Service:
        def __init__(self) -> None:
            self.request_ids: list[str] = []

        async def start_chat(
            self, messages, *, provider=None, model=None, request_id=None
        ) -> str:
            self.request_ids.append(request_id)
            return request_id

    service = _Service()
    controller = ChatController(
        state,
        service,
        None,
        conversations=StubConversationPersistence(),
        request_id_factory=lambda: "quick-request",
    )
    wm = WindowManager(state, controller)

    wm.show_window(Mode.FLOATING)
    floating = wm.window(Mode.FLOATING)
    floating.command_input.setText("hello")
    floating.command_input.returnPressed.emit()

    assert wm.is_visible(Mode.WORKSPACE)
    assert [m.content for m in controller.messages] == ["hello", ""]
    assert floating.command_input.text() == ""

    await controller.wait_idle()
    assert service.request_ids == ["quick-request"]
