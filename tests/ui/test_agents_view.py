"""UI tests for Phase 4.5 EventBridge, AgentsView, and HudWindow activity feed."""

from __future__ import annotations

import asyncio

from jarvis.core.event_bus import EventBus
from jarvis.core.events import (
    AgentRunFinished,
    AgentRunStarted,
    AgentStepUpdated,
)
from jarvis.desktop.adapters.event_bridge import EventBridge
from jarvis.desktop.viewmodels.app_state import AppState
from jarvis.desktop.views.agents_view import AgentsView
from jarvis.desktop.windows.hud_window import HudWindow
from jarvis.desktop.windows.workspace_window import WorkspaceWindow


class _FakeAgentManager:
    def __init__(self) -> None:
        self.cancelled: list[str] = []

    async def cancel(self, run_id: str) -> bool:
        self.cancelled.append(run_id)
        return True


async def test_event_bridge_updates_agents_view_and_hud(qtbot) -> None:
    bus = EventBus()
    state = AppState()
    EventBridge(bus, state)
    mgr = _FakeAgentManager()

    view = AgentsView(state, agent_manager=mgr)
    hud = HudWindow(state)
    qtbot.addWidget(view)
    qtbot.addWidget(hud)

    await bus.publish(AgentRunStarted(run_id="r12345678", agent_id="research"))
    await bus.publish(
        AgentStepUpdated(
            run_id="r12345678", agent_id="research", step="indexing", progress=0.5
        )
    )

    assert view.active_list.count() == 1
    assert "research" in view.active_list.item(0).text()
    assert "indexing" in hud.agent_status_label.text()

    # Cancel button calls AgentManager.cancel(run_id).
    view.active_list.setCurrentRow(0)
    view.cancel_button.click()
    for _ in range(20):
        if mgr.cancelled:
            break
        await asyncio.sleep(0)
    assert mgr.cancelled == ["r12345678"]

    await bus.publish(
        AgentRunFinished(
            run_id="r12345678",
            agent_id="research",
            status="completed",
            duration_ms=42,
        )
    )
    assert view.active_list.count() == 0
    assert view.history_list.count() == 1
    assert hud.agent_status_label.text() == "No active agents"
    assert hud.agent_timeline.count() == 1


def test_workspace_window_embeds_agents_view(qtbot) -> None:
    ws = WorkspaceWindow(AppState(), None)
    qtbot.addWidget(ws)
    assert isinstance(ws.agents_view, AgentsView)
