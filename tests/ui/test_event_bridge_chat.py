"""EventBridge → AppState forwarding for the Stage 2.6 chat events."""

from __future__ import annotations

import pytest

from jarvis.core.event_bus import EventBus
from jarvis.core.events import (
    AssistantStateChanged,
    ChatStreamCancelled,
    ChatStreamCompleted,
    ChatStreamFailed,
    ChatStreamStarted,
    ChatToken,
)
from jarvis.desktop.adapters.event_bridge import EventBridge
from jarvis.desktop.viewmodels.app_state import AppState


@pytest.fixture()
def bridge(qtbot):
    bus = EventBus()
    state = AppState()
    return bus, state, EventBridge(bus, state)


async def test_chat_events_reach_shared_state(bridge) -> None:
    bus, state, _ = bridge
    started: list[tuple[str, str, str, str]] = []
    tokens: list[tuple[str, int, str]] = []
    completed: list[tuple[str, str, int]] = []
    failed: list[tuple[str, str, str]] = []
    cancelled: list[tuple[str, str]] = []
    states: list[str] = []

    state.chat_stream_started.connect(lambda *args: started.append(args))
    state.chat_token.connect(lambda *args: tokens.append(args))
    state.chat_stream_completed.connect(lambda *args: completed.append(args))
    state.chat_stream_failed.connect(lambda *args: failed.append(args))
    state.chat_stream_cancelled.connect(lambda *args: cancelled.append(args))
    state.assistant_state_changed.connect(states.append)

    await bus.publish(
        ChatStreamStarted(
            request_id="r1", provider="ollama", model="qwen3:8b", privacy="local"
        )
    )
    await bus.publish(ChatToken(request_id="r1", seq=0, text="hi"))
    await bus.publish(
        ChatStreamCompleted(request_id="r1", content="hi", tokens_used=1)
    )
    await bus.publish(
        ChatStreamFailed(request_id="r2", detail="boom", partial_content="par")
    )
    await bus.publish(
        ChatStreamCancelled(request_id="r3", partial_content="partial")
    )
    await bus.publish(AssistantStateChanged(state="speaking"))

    assert started == [("r1", "ollama", "qwen3:8b", "local")]
    assert tokens == [("r1", 0, "hi")]
    assert completed == [("r1", "hi", 1)]
    assert failed == [("r2", "boom", "par")]
    assert cancelled == [("r3", "partial")]
    assert states == ["speaking"]
    assert state.assistant_state == "speaking"


async def test_started_privacy_field_is_additive_default(bridge) -> None:
    bus, state, _ = bridge
    privacy: list[str] = []
    state.chat_stream_started.connect(
        lambda _rid, _provider, _model, value: privacy.append(value)
    )
    await bus.publish(
        ChatStreamStarted(request_id="r1", provider="ollama", model="m")
    )
    assert privacy == [""]
