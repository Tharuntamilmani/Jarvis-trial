"""UI smoke tests for the three windows and window lifecycle semantics.

Uses pytest-qt with the real (non-qasync) Qt loop — the windows only bind
to the shared AppState, so no core engine is required here.
"""

from __future__ import annotations

import pytest

from jarvis.desktop.viewmodels.app_state import AppState
from jarvis.desktop.window_manager import Mode, WindowManager
from jarvis.desktop.windows.floating_window import FloatingWindow
from jarvis.desktop.windows.hud_window import HudWindow
from jarvis.desktop.windows.workspace_window import WorkspaceWindow


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
