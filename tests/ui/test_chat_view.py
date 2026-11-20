"""Workspace ChatView integration: rendering, composer, consent, lifecycle."""

from __future__ import annotations

import asyncio
import time

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from jarvis.core.engine import ProviderStatus
from jarvis.core.event_bus import EventBus
from jarvis.core.events import CloudFallbackProposed
from jarvis.desktop.viewmodels.app_state import AppState
from jarvis.desktop.viewmodels.chat_controller import ChatController
from jarvis.desktop.viewmodels.chat_models import STATUS_COMPLETED
from jarvis.desktop.views.chat_view import ChatView
from jarvis.desktop.windows.workspace_window import WorkspaceWindow
from jarvis.intelligence.chat_service import CancelOutcome
from jarvis.intelligence.cloud_payload import build_cloud_payload
from jarvis.intelligence.consent import ConsentManager
from jarvis.intelligence.prompts import JARVIS_SYSTEM_PROMPT
from jarvis.intelligence.providers.base import ChatMessage, ChatRequest
from tests.unit.chat_helpers import chat_settings


class FakeChatService:
    def __init__(self) -> None:
        self.starts: list[dict] = []
        self.cancels: list[str] = []
        self.gate: asyncio.Event | None = None

    async def start_chat(
        self, messages, *, provider=None, model=None, request_id=None
    ):
        self.starts.append({"messages": list(messages), "request_id": request_id})
        if self.gate is not None:
            await self.gate.wait()
        return request_id

    async def cancel(self, request_id: str) -> CancelOutcome:
        self.cancels.append(request_id)
        return CancelOutcome.CANCELLED


class FakeProviderSource:
    def __init__(self) -> None:
        self.refresh_calls = 0
        self.statuses = {
            "ollama": ProviderStatus(
                provider="ollama",
                available=True,
                detail="connected",
                privacy="local",
                configured=True,
                model="qwen3:8b",
            ),
            "groq": ProviderStatus(
                provider="groq",
                available=False,
                detail="not configured",
                privacy="cloud",
                configured=False,
                model="llama-3.3-70b-versatile",
            ),
        }

    def provider_status_snapshot(self):
        return dict(self.statuses)

    async def refresh_provider_statuses(self):
        self.refresh_calls += 1
        return dict(self.statuses)


def _controller(
    service: FakeChatService | None = None,
    consent: ConsentManager | None = None,
    source: FakeProviderSource | None = None,
):
    state = AppState()
    controller = ChatController(
        state,
        service or FakeChatService(),
        consent,
        provider_source=source,
    )
    return state, controller


def _consent_pair(rid: str, user_message: str):
    request = ChatRequest(
        model=None,
        messages=[ChatMessage(role="user", content=user_message)],
        request_id=rid,
    )
    payload = build_cloud_payload(
        request, system_prompt=JARVIS_SYSTEM_PROMPT
    )
    proposal = CloudFallbackProposed(
        request_id=rid,
        reason="local model offline",
        provider="groq",
        model="",
        payload_preview=payload.preview(),
        payload_fingerprint=payload.fingerprint,
        scope=payload.scope_description,
    )
    return payload, proposal


async def _until(predicate, timeout: float = 1.0) -> None:
    for _ in range(int(timeout / 0.005)):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition was not met")


# -- Shell / welcome -----------------------------------------------------------


def test_workspace_chat_page_and_status_mapping(qtbot) -> None:
    state, controller = _controller()
    ws = WorkspaceWindow(state, controller)
    qtbot.addWidget(ws)

    assert isinstance(ws.content.widget(0), ChatView)
    ws.nav.setCurrentRow(5)
    assert ws.content.currentIndex() == 5
    assert ws._chips["engine"] is not None


def test_welcome_screen_appears_with_no_messages(qtbot) -> None:
    state, controller = _controller()
    view = ChatView(state, controller)
    qtbot.addWidget(view)

    assert view.is_welcome_visible is True
    assert view.bubbles == {}
    assert view.content_stack.currentIndex() == 0


# -- Composer / sending ---------------------------------------------------------


async def test_sending_renders_one_user_and_one_assistant_bubble(qtbot) -> None:
    service = FakeChatService()
    state, controller = _controller(service=service)
    view = ChatView(state, controller)
    qtbot.addWidget(view)

    view.input.setPlainText("hello JARVIS")
    assert view.send_button.isEnabled()
    view.send_button.click()
    await controller.wait_idle()

    assert [m.role for m in controller.messages] == ["user", "assistant"]
    assert len(view.bubbles) == 2
    assert view.is_welcome_visible is False
    user_bubble = view.bubbles[controller.messages[0].id]
    assistant_bubble = view.bubbles[controller.messages[1].id]
    assert user_bubble.content_label.text() == "hello JARVIS"
    assert assistant_bubble.content_label.text() in ("…", " ▍")


def test_assistant_state_and_privacy_status_render(qtbot) -> None:
    state, controller = _controller()
    view = ChatView(state, controller)
    qtbot.addWidget(view)

    state.set_assistant_state("thinking")
    assert view.assistant_status.text() == "THINKING"
    state.set_assistant_state("speaking")
    assert view.assistant_status.text() == "SPEAKING"
    assert "LOCAL" in view.provider_pill.text()


async def test_user_bubbles_align_right_assistant_left(qtbot) -> None:
    from PySide6.QtCore import QPoint

    state, controller = _controller()
    view = ChatView(state, controller)
    qtbot.addWidget(view)
    view.show()
    qtbot.waitExposed(view)
    rid = controller.send("hello")
    await controller.wait_idle()
    state.apply_chat_stream_completed(rid, "hi", 1)
    QTest.qWait(30)

    user = view.bubbles[controller.messages[0].id]
    assistant = view.bubbles[controller.messages[1].id]
    assert user.mapTo(view, QPoint(0, 0)).x() > assistant.mapTo(
        view, QPoint(0, 0)
    ).x()


async def test_enter_sends_and_shift_enter_inserts_newline(qtbot) -> None:
    service = FakeChatService()
    state, controller = _controller(service=service)
    view = ChatView(state, controller)
    qtbot.addWidget(view)
    view.show()
    qtbot.waitExposed(view)

    view.input.setFocus()
    view.input.setPlainText("first line")
    QTest.keyClick(view.input, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert "\n" in view.input.toPlainText()
    assert controller.messages == ()

    QTest.keyClick(view.input, Qt.Key.Key_Return)
    await controller.wait_idle()
    assert controller.messages
    assert controller.messages[0].content == "first line"
    assert view.input.toPlainText() == ""


async def test_whitespace_only_message_is_not_sent(qtbot) -> None:
    service = FakeChatService()
    state, controller = _controller(service=service)
    view = ChatView(state, controller)
    qtbot.addWidget(view)

    view.input.setPlainText("   \n  ")
    assert view.send_button.isEnabled() is False
    view.send_button.click()
    await asyncio.sleep(0)
    assert service.starts == []
    assert controller.messages == ()


async def test_streaming_renders_incrementally_then_exact_final_content(qtbot) -> None:
    service = FakeChatService()
    state, controller = _controller(service=service)
    view = ChatView(state, controller)
    qtbot.addWidget(view)

    rid = controller.send("hi")
    await controller.wait_idle()
    state.apply_chat_stream_started(rid, "ollama", "qwen3:8b", "local")
    assistant_id = controller.messages[-1].id
    bubble = view.bubbles[assistant_id]

    state.apply_chat_token(rid, 0, "Hel")
    assert bubble.content_label.text() == "Hel ▍"
    await asyncio.sleep(0.05)  # coalescing window
    state.apply_chat_token(rid, 1, "lo")
    assert bubble.content_label.text() == "Hello ▍"

    state.apply_chat_stream_completed(rid, "Hello!", 2)
    assert bubble.content_label.text() == "Hello!"
    assert controller.messages[-1].status == STATUS_COMPLETED
    assert view.stop_button.isHidden() is True


async def test_busy_state_shows_stop_and_stop_cancels_once(qtbot) -> None:
    service = FakeChatService()
    state, controller = _controller(service=service)
    view = ChatView(state, controller)
    qtbot.addWidget(view)

    view.input.setPlainText("work")
    view.send_button.click()
    await controller.wait_idle()

    assert view.send_button.isEnabled() is False
    assert view.stop_button.isHidden() is False
    assert view.input.isEnabled() is True  # composer stays editable

    view.stop_button.click()
    await controller.wait_idle()
    assert service.cancels == [controller.active_request_id]
    state.apply_chat_stream_cancelled(controller.active_request_id, "")
    assert view.stop_button.isHidden() is True


async def test_send_is_non_blocking_and_ui_remains_usable(qtbot) -> None:
    service = FakeChatService()
    service.gate = asyncio.Event()
    state, controller = _controller(service=service)
    view = ChatView(state, controller)
    qtbot.addWidget(view)

    started = time.perf_counter()
    view.input.setPlainText("slow request")
    view.send_button.click()
    elapsed = time.perf_counter() - started

    assert elapsed < 0.25
    assert len(view.bubbles) == 2
    assert view.input.isEnabled() is True

    service.gate.set()
    await controller.wait_idle()


async def test_kill_switch_disables_sending(qtbot) -> None:
    state, controller = _controller()
    view = ChatView(state, controller)
    qtbot.addWidget(view)

    state.set_kill_switch(True)
    view.input.setPlainText("blocked")
    assert view.send_button.isEnabled() is False
    assert view.kill_pill.isHidden() is False
    view.send_button.click()
    assert controller.messages == ()

    state.set_kill_switch(False)
    assert view.send_button.isEnabled() is True


# -- Consent overlay -----------------------------------------------------------


async def _open_consent(qtbot, view, controller, consent, rid, message="current"):
    payload, proposal = _consent_pair(rid, message)
    task = asyncio.create_task(
        consent.request_consent(
            request_id=rid, proposal=proposal, payload=payload
        )
    )
    await _until(lambda: controller.current_consent is not None)
    assert view.consent_overlay.isHidden() is False
    return payload, task


async def test_consent_overlay_shows_exact_payload_and_approves(qtbot) -> None:
    service = FakeChatService()
    consent = ConsentManager(_consent_settings(), EventBus())
    state, controller = _controller(service=service, consent=consent)
    view = ChatView(state, controller)
    qtbot.addWidget(view)

    rid = controller.send("current message")
    await controller.wait_idle()
    payload, task = await _open_consent(
        qtbot, view, controller, consent, rid, "current message"
    )
    overlay = view.consent_overlay
    assert overlay._system_prompt.toPlainText() == JARVIS_SYSTEM_PROMPT
    assert overlay._user_message.toPlainText() == "current message"
    assert overlay._provider.text() == "groq"
    assert "API" not in overlay._model.text()

    overlay.approve_button.click()
    decision = await task
    assert decision.granted is True
    assert overlay.isHidden() is True


async def test_consent_reject_and_close_deny(qtbot) -> None:
    service = FakeChatService()
    consent = ConsentManager(_consent_settings(), EventBus())
    state, controller = _controller(service=service, consent=consent)
    view = ChatView(state, controller)
    qtbot.addWidget(view)

    rid = controller.send("current")
    await controller.wait_idle()
    payload, task = await _open_consent(qtbot, view, controller, consent, rid)
    view.consent_overlay.reject_button.click()
    assert (await task).granted is False

    # A new active turn + prompt can be closed instead (same denial path).
    state.apply_chat_stream_completed(rid, "done", 1)
    rid2 = controller.send("second")
    await controller.wait_idle()
    _, task2 = await _open_consent(qtbot, view, controller, consent, rid2)
    view.consent_overlay.close_button.click()
    assert (await task2).granted is False


async def test_escape_denies_consent(qtbot) -> None:
    service = FakeChatService()
    consent = ConsentManager(_consent_settings(), EventBus())
    state, controller = _controller(service=service, consent=consent)
    view = ChatView(state, controller)
    qtbot.addWidget(view)
    view.show()
    qtbot.waitExposed(view)

    rid = controller.send("current")
    await controller.wait_idle()
    _, task = await _open_consent(qtbot, view, controller, consent, rid)

    view.input.setFocus()
    QTest.keyClick(view, Qt.Key.Key_Escape)
    assert (await task).granted is False
    assert view.consent_overlay.isHidden() is True


async def test_cancellation_hides_consent_card(qtbot) -> None:
    service = FakeChatService()
    consent = ConsentManager(_consent_settings(), EventBus())
    state, controller = _controller(service=service, consent=consent)
    view = ChatView(state, controller)
    qtbot.addWidget(view)

    rid = controller.send("current")
    await controller.wait_idle()
    _, task = await _open_consent(qtbot, view, controller, consent, rid)

    controller.stop()
    assert (await task).granted is False
    await _until(lambda: view.consent_overlay.isHidden() is True)


# -- Related UI / lifecycle ------------------------------------------------------


async def test_reopened_workspace_restores_pending_consent(qtbot) -> None:
    from jarvis.desktop.window_manager import Mode, WindowManager

    service = FakeChatService()
    consent = ConsentManager(_consent_settings(), EventBus())
    state, controller = _controller(service=service, consent=consent)
    wm = WindowManager(state, controller)
    wm.switch_mode(Mode.WORKSPACE)
    rid = controller.send("current")
    await controller.wait_idle()
    view = wm.window(Mode.WORKSPACE).findChild(ChatView)
    _, task = await _open_consent(qtbot, view, controller, consent, rid)

    wm.close_window(Mode.WORKSPACE)
    wm.switch_mode(Mode.WORKSPACE)
    reopened = wm.window(Mode.WORKSPACE).findChild(ChatView)
    assert reopened.consent_overlay.isHidden() is False

    reopened.consent_overlay.reject_button.click()
    assert (await task).granted is False


async def test_provider_status_refresh_on_first_show_only(qtbot) -> None:
    source = FakeProviderSource()
    state, controller = _controller(source=source)
    ws = WorkspaceWindow(state, controller)
    qtbot.addWidget(ws)

    ws.show()
    qtbot.waitExposed(ws)
    await controller.wait_provider_refresh()
    assert source.refresh_calls == 1
    assert ws._provider_details["ollama"].text() == "qwen3:8b"

    ws.hide()
    ws.show()
    await controller.wait_provider_refresh()
    assert source.refresh_calls == 1

    ws.refresh_button.click()
    await controller.wait_provider_refresh()
    assert source.refresh_calls == 2


async def test_window_close_does_not_cancel_generation_and_reopen_restores(qtbot) -> None:
    service = FakeChatService()
    state, controller = _controller(service=service)
    ws = WorkspaceWindow(state, controller)
    qtbot.addWidget(ws)

    rid = controller.send("keep going")
    await controller.wait_idle()
    state.apply_chat_stream_started(rid, "ollama", "qwen3:8b", "local")
    state.apply_chat_token(rid, 0, "partial")

    ws.close()
    assert controller.busy is True
    assert service.cancels == []
    assert len(controller.messages) == 2

    reopened = WorkspaceWindow(state, controller)
    qtbot.addWidget(reopened)
    assert len(reopened.findChild(ChatView).bubbles) == 2
    assistant = reopened.findChild(ChatView).bubbles[controller.messages[-1].id]
    assert assistant.content_label.text() == "partial ▍"


async def test_window_manager_close_keeps_generation_and_reopen_restores(qtbot) -> None:
    from jarvis.desktop.window_manager import Mode, WindowManager

    service = FakeChatService()
    state, controller = _controller(service=service)
    wm = WindowManager(state, controller)
    wm.switch_mode(Mode.WORKSPACE)
    rid = controller.send("keep going")
    await controller.wait_idle()
    state.apply_chat_stream_started(rid, "ollama", "qwen3:8b", "local")
    state.apply_chat_token(rid, 0, "partial")

    wm.close_window(Mode.WORKSPACE)
    assert controller.busy is True
    assert service.cancels == []

    wm.switch_mode(Mode.WORKSPACE)
    view = wm.window(Mode.WORKSPACE).findChild(ChatView)
    assert len(view.bubbles) == 2


async def test_new_chat_resets_view_and_controller(qtbot) -> None:
    state, controller = _controller()
    ws = WorkspaceWindow(state, controller)
    qtbot.addWidget(ws)
    view = ws.findChild(ChatView)

    rid = controller.send("hello")
    await controller.wait_idle()
    state.apply_chat_stream_completed(rid, "hi", 1)
    assert len(view.bubbles) == 2

    ws.new_chat_button.click()
    await controller.wait_idle()
    assert controller.messages == ()
    assert view.bubbles == {}
    assert view.is_welcome_visible is True


async def test_ui_slots_run_on_the_qt_main_thread(qtbot) -> None:
    from PySide6.QtWidgets import QApplication

    state, controller = _controller()
    view = ChatView(state, controller)
    qtbot.addWidget(view)
    threads = []
    controller.message_updated.connect(
        lambda _message: threads.append(QApplication.instance().thread())
    )
    rid = controller.send("thread probe")
    await controller.wait_idle()
    state.apply_chat_stream_completed(rid, "done", 1)

    assert threads
    assert all(
        thread is QApplication.instance().thread() for thread in threads
    )


def _consent_settings():
    from pathlib import Path
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="jarvis-chatview-"))
    return chat_settings(root)
