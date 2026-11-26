"""ChatController durability: persist-before-dispatch, rollback, finalisation."""

from __future__ import annotations

import asyncio

from jarvis.conversations.models import (
    ErrorCategory,
    PersistedTurn,
    safe_error_display,
)
from jarvis.core.exceptions import KillSwitchActiveError
from jarvis.desktop.viewmodels.app_state import AppState
from jarvis.desktop.viewmodels.chat_controller import ChatController
from jarvis.desktop.viewmodels.chat_models import (
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_PENDING,
)
from jarvis.desktop.views.chat_view import ChatView
from jarvis.desktop.windows.workspace_window import WorkspaceWindow
from jarvis.intelligence.chat_service import CancelOutcome, ChatServiceError
from tests.ui.chat_test_helpers import make_stored_message


class FakeChatService:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.starts: list[str] = []
        self.cancels: list[str] = []
        self.start_error: BaseException | None = None

    async def start_chat(
        self, messages, *, provider=None, model=None, request_id=None
    ) -> str:
        self.events.append("start")
        self.starts.append(request_id)
        if self.start_error is not None:
            raise self.start_error
        return request_id

    async def cancel(self, request_id: str) -> CancelOutcome:
        self.cancels.append(request_id)
        return CancelOutcome.CANCELLED


class FakeChatServiceWithProvider(FakeChatService):
    def __init__(self, events: list[str]) -> None:
        super().__init__(events)
        self.start_calls: list[dict] = []

    async def start_chat(
        self, messages, *, provider=None, model=None, request_id=None
    ) -> str:
        self.start_calls.append(
            {"provider": provider, "model": model, "request_id": request_id}
        )
        return await super().start_chat(
            messages, provider=provider, model=model, request_id=request_id
        )


class FakePersistence:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.fail_initial = False
        self.fail_dispatch = False
        self.finalize_result = True
        self.finalize_gate: asyncio.Event | None = None
        self.checkpoints: list[tuple[str, str, int]] = []
        self.finals: list[dict] = []
        self.stored: dict[str, list] = {}
        self.active_id: str | None = None
        self.drain_calls = 0

    async def persist_initial_turn(
        self, *, request_id, user_content, conversation_id=None
    ) -> PersistedTurn:
        self.events.append("persist")
        if self.fail_initial:
            raise RuntimeError("database unavailable")
        conv_id = conversation_id or ("d" * 32)
        self.active_id = conv_id
        return PersistedTurn(
            conversation_id=conv_id,
            request_id=request_id,
            assistant_message_id="e" * 32,
            user_message_id="f" * 32,
            user_seq=1,
            assistant_seq=2,
        )

    async def mark_streaming(self, turn: PersistedTurn) -> int:
        self.events.append("dispatch")
        if self.fail_dispatch:
            raise RuntimeError("dispatch state rejected")
        return 1

    async def write_checkpoint(self, message_id, content, expected_version):
        self.events.append("checkpoint")
        self.checkpoints.append((message_id, content, expected_version))
        return expected_version + 1

    async def finalize(
        self,
        message_id,
        *,
        status,
        content,
        expected_version,
        error=None,
        tokens=0,
        provider=None,
        model=None,
        privacy=None,
    ) -> bool:
        self.events.append("finalize")
        if self.finalize_gate is not None:
            await self.finalize_gate.wait()
        self.finals.append(
            {
                "message_id": message_id,
                "status": status,
                "content": content,
                "error": error,
                "version": expected_version,
                "tokens": tokens,
                "provider": provider,
                "model": model,
                "privacy": privacy,
            }
        )
        return self.finalize_result

    async def load_conversation(self, conversation_id: str, *, limit: int = 200):
        return list(self.stored.get(conversation_id, []))[-limit:]

    async def get_active_conversation_id(self) -> str | None:
        return self.active_id

    async def drain_reconcile(self) -> None:
        self.drain_calls += 1

    async def persist_retry(
        self,
        *,
        conversation_id: str,
        request_id: str,
        retry_of_request_id: str,
        content: str = "",
    ) -> PersistedTurn:
        self.events.append("persist_retry")
        return PersistedTurn(
            conversation_id=conversation_id,
            request_id=request_id,
            assistant_message_id="r" * 32,
            user_message_id=None,
            user_seq=None,
            assistant_seq=3,
            retry_of_request_id=retry_of_request_id,
        )

    async def list_conversations(self, *, limit: int = 100):
        return list(getattr(self, "summaries", []))[:limit]

    async def set_active_conversation(self, conversation_id: str) -> None:
        self.active_id = conversation_id

    async def rename_conversation(self, conversation_id: str, title: str):
        for idx, s in enumerate(getattr(self, "summaries", [])):
            if s.id == conversation_id:
                from dataclasses import replace

                self.summaries[idx] = replace(s, title=title)
                return self.summaries[idx]
        raise RuntimeError("not found")

    async def delete_conversation(self, conversation_id: str) -> bool:
        summaries = getattr(self, "summaries", [])
        before = len(summaries)
        self.summaries = [s for s in summaries if s.id != conversation_id]
        self.stored.pop(conversation_id, None)
        if self.active_id == conversation_id:
            self.active_id = None
        return len(self.summaries) < before


def _build(persistence: FakePersistence | None = None):
    events: list[str] = persistence.events if persistence is not None else []
    service = FakeChatService(events)
    state = AppState()
    controller = ChatController(
        state,
        service,
        None,
        conversations=persistence,
        install_consent=False,
    )
    return controller, service, state, events


async def _settle() -> None:
    for _ in range(4):
        await asyncio.sleep(0)


# -- Durable gate -------------------------------------------------------------


async def test_send_stays_synchronous_and_persists_before_dispatch() -> None:
    persistence = FakePersistence([])
    controller, service, _state, events = _build(persistence)

    request_id = controller.send("hello")

    assert isinstance(request_id, str)
    assert len(controller.messages) == 2
    assert controller.messages[1].status == STATUS_PENDING

    await controller.wait_idle()

    assert events[:3] == ["persist", "dispatch", "start"]
    assert service.starts == [request_id]
    assert controller.conversation_id == "d" * 32


async def test_persistence_failure_rolls_back_and_never_dispatches() -> None:
    persistence = FakePersistence([])
    persistence.fail_initial = True
    controller, _service, _state, events = _build(persistence)
    restored: list[str] = []
    errors: list[str] = []
    controller.input_restored.connect(restored.append)
    controller.error_occurred.connect(errors.append)

    request_id = controller.send("hello")
    await controller.wait_idle()

    assert request_id is not None
    assert "start" not in events
    assert controller.messages == ()
    assert restored == ["hello"]
    assert errors and "could not be saved" in errors[0]
    assert controller.conversation_id is None
    assert controller.busy is False


async def test_rollback_keeps_messages_from_other_requests() -> None:
    persistence = FakePersistence([])
    controller, _service, state, _events = _build(persistence)

    first = controller.send("first")
    await controller.wait_idle()
    state.apply_chat_stream_started(first, "ollama", "m", "local")
    state.apply_chat_stream_completed(first, "answer", 3)
    await controller.wait_idle()
    assert len(controller.messages) == 2

    persistence.fail_initial = True
    controller.send("second")
    await controller.wait_idle()

    assert [m.content for m in controller.messages] == ["first", "answer"]


async def test_dispatch_state_failure_marks_failed_without_dispatch() -> None:
    persistence = FakePersistence([])
    persistence.fail_dispatch = True
    controller, _service, _state, events = _build(persistence)

    controller.send("hello")
    await controller.wait_idle()

    assert "start" not in events
    message = controller.messages[-1]
    assert message.status == STATUS_FAILED
    assert message.error_category == "persistence_error"
    assert persistence.finals[-1]["status"] == "failed"
    assert persistence.finals[-1]["error"] == "persistence_error"


# -- Terminal persistence -----------------------------------------------------


async def test_completion_persists_final_content_and_status() -> None:
    persistence = FakePersistence([])
    controller, _service, state, _events = _build(persistence)

    request_id = controller.send("hi")
    await controller.wait_idle()
    state.apply_chat_stream_started(request_id, "ollama", "qwen3:8b", "local")
    state.apply_chat_token(request_id, 0, "hello")
    state.apply_chat_stream_completed(request_id, "hello there", 4)
    await controller.wait_idle()

    final = persistence.finals[-1]
    assert final["status"] == "completed"
    assert final["content"] == "hello there"
    assert final["provider"] == "ollama"
    assert final["privacy"] == "local"


async def test_cancellation_persists_partial_content() -> None:
    persistence = FakePersistence([])
    controller, _service, state, _events = _build(persistence)

    request_id = controller.send("hi")
    await controller.wait_idle()
    state.apply_chat_stream_started(request_id, "ollama", "m", "local")
    state.apply_chat_token(request_id, 0, "partial")
    state.apply_chat_stream_cancelled(request_id, "partial")
    await controller.wait_idle()

    final = persistence.finals[-1]
    assert final["status"] == "cancelled"
    assert final["content"] == "partial"
    assert final["error"] == "cancelled"


async def test_failure_persists_provider_error_category() -> None:
    persistence = FakePersistence([])
    controller, _service, state, _events = _build(persistence)

    request_id = controller.send("hi")
    await controller.wait_idle()
    state.apply_chat_stream_started(request_id, "ollama", "m", "local")
    state.apply_chat_stream_failed(request_id, "boom", "partial")
    await controller.wait_idle()

    final = persistence.finals[-1]
    assert final["status"] == "failed"
    assert final["error"] == "provider_error"
    assert final["content"] == "partial"


async def test_tokens_trigger_checkpoints() -> None:
    persistence = FakePersistence([])
    controller, _service, state, _events = _build(persistence)

    request_id = controller.send("hi")
    await controller.wait_idle()
    state.apply_chat_stream_started(request_id, "ollama", "m", "local")
    state.apply_chat_token(request_id, 0, "a" * 300)
    await _settle()

    assert persistence.checkpoints
    assert persistence.checkpoints[0][1] == "a" * 300


# -- New Chat -----------------------------------------------------------------


async def test_new_chat_is_synchronous_and_defers_the_switch() -> None:
    persistence = FakePersistence([])
    controller, service, state, _events = _build(persistence)

    request_id = controller.send("hi")
    await controller.wait_idle()
    state.apply_chat_stream_started(request_id, "ollama", "m", "local")
    state.apply_chat_token(request_id, 0, "partial")

    assert controller.new_chat() is None
    assert controller.finalizing is True
    assert controller.can_send("next") is False
    assert len(controller.messages) == 2  # not switched yet

    await controller.wait_idle()

    assert controller.messages == ()
    assert controller.finalizing is False
    assert request_id in service.cancels
    assert persistence.finals[-1]["status"] == "cancelled"
    assert controller.conversation_id is None


async def test_new_chat_keeps_conversation_when_finalisation_fails() -> None:
    persistence = FakePersistence([])
    persistence.finalize_result = False
    controller, _service, state, _events = _build(persistence)
    errors: list[str] = []
    controller.error_occurred.connect(errors.append)

    request_id = controller.send("hi")
    await controller.wait_idle()
    state.apply_chat_stream_started(request_id, "ollama", "m", "local")
    state.apply_chat_token(request_id, 0, "partial")

    controller.new_chat()
    await controller.wait_idle()

    assert len(controller.messages) == 2
    assert controller.conversation_id == "d" * 32
    assert controller.finalizing is False
    assert errors and "not switched" in errors[0]


async def test_missing_persistence_fails_closed() -> None:
    controller, service, _state, events = _build(None)
    errors: list[str] = []
    controller.error_occurred.connect(errors.append)

    assert controller.persistence_available is False
    assert controller.persistence_unavailable is True
    assert controller.can_send("hi") is False
    assert controller.send("hi") is None
    assert errors and "persistence is unavailable" in errors[0].lower()
    assert controller.messages == ()
    assert "start" not in events
    assert service.starts == []

    # Direct _dispatch must also fail closed without invoking provider.
    await controller._dispatch("r1", [], "hi")  # noqa: SLF001
    assert service.starts == []


async def test_new_chat_refuses_to_discard_when_persistence_unavailable() -> None:
    persistence = FakePersistence([])
    controller, _service, state, _events = _build(persistence)
    rid = controller.send("important message")
    await controller.wait_idle()
    state.apply_chat_stream_completed(rid, "important reply", 2)
    await controller.wait_idle()

    controller.mark_persistence_unavailable()
    errors: list[str] = []
    controller.error_occurred.connect(errors.append)
    controller.new_chat()

    assert len(controller.messages) == 2
    assert errors and "not discarded" in errors[0]


def test_chat_view_shows_persistent_unavailable_state(qtbot) -> None:
    controller, _service, state, _events = _build(None)
    view = ChatView(state, controller)
    qtbot.addWidget(view)

    assert view.input.isEnabled() is False
    assert view.send_button.isEnabled() is False
    assert view.error_label.isHidden() is False
    assert "persistence is unavailable" in view.error_label.text().lower()
    assert view.persistence_pill.isHidden() is False

    # Remains persistent across status refreshes.
    state.set_assistant_state("idle")
    assert view.error_label.isHidden() is False
    assert view.send_button.isEnabled() is False
    assert view.persistence_pill.isHidden() is False


def test_workspace_disables_new_chat_when_persistence_unavailable(qtbot) -> None:
    controller, _service, state, _events = _build(None)
    ws = WorkspaceWindow(state, controller)
    qtbot.addWidget(ws)

    assert ws.new_chat_button.isEnabled() is False


async def test_chat_view_restores_input_on_persistence_failure(qtbot) -> None:
    persistence = FakePersistence([])
    persistence.fail_initial = True
    controller, _service, state, _events = _build(persistence)
    view = ChatView(state, controller)
    qtbot.addWidget(view)

    view.input.setPlainText("preserve my prompt")
    view.send_button.click()
    await controller.wait_idle()

    assert view.input.toPlainText() == "preserve my prompt"
    assert controller.messages == ()


# -- Finding 1: Error persistence & reload ------------------------------------


async def test_fail_start_persists_terminal_error_and_never_leaves_streaming() -> None:
    for err, expected_status, expected_cat in (
        (
            ChatServiceError("provider down"),
            "failed",
            ErrorCategory.PROVIDER_ERROR.value,
        ),
        (
            KillSwitchActiveError("kill switch"),
            "failed",
            ErrorCategory.PROVIDER_ERROR.value,
        ),
        (TimeoutError("slow"), "failed", ErrorCategory.TIMEOUT.value),
        (RuntimeError("unexpected"), "failed", ErrorCategory.PROVIDER_ERROR.value),
    ):
        persistence = FakePersistence([])
        controller, service, _state, _events = _build(persistence)
        service.start_error = err

        controller.send("hello")
        await controller.wait_idle()

        assert persistence.finals, f"expected finalize for {type(err).__name__}"
        final = persistence.finals[-1]
        assert final["status"] == expected_status
        assert final["error"] == expected_cat
        assert controller.messages[-1].status == expected_status
        assert controller.messages[-1].error_category == expected_cat
        assert controller.busy is False


async def test_fail_start_cancellation_persists_cancelled_category() -> None:
    persistence = FakePersistence([])
    controller, service, _state, _events = _build(persistence)
    service.start_error = asyncio.CancelledError()

    controller.send("hello")
    await controller.wait_idle()

    final = persistence.finals[-1]
    assert final["status"] == "cancelled"
    assert final["error"] == ErrorCategory.CANCELLED.value
    assert controller.messages[-1].status == STATUS_CANCELLED
    assert controller.messages[-1].error_category == ErrorCategory.CANCELLED.value


async def test_load_conversation_restores_error_category_and_safe_display() -> None:
    persistence = FakePersistence([])
    persistence.stored["c" * 32] = [
        make_stored_message(
            id="1" * 32,
            conversation_id="c" * 32,
            role="user",
            content="first question",
            seq=1,
            status="completed",
        ),
        make_stored_message(
            id="2" * 32,
            conversation_id="c" * 32,
            role="assistant",
            content="",
            seq=2,
            status="failed",
            error=ErrorCategory.PROVIDER_ERROR.value,
        ),
        make_stored_message(
            id="3" * 32,
            conversation_id="c" * 32,
            role="assistant",
            content="partial text",
            seq=3,
            status="streaming",
        ),
    ]
    controller, _service, _state, _events = _build(persistence)

    loaded = await controller.load_conversation("c" * 32)
    assert loaded is True
    assert controller.conversation_id == "c" * 32
    assert len(controller.messages) == 3

    failed_msg = controller.messages[1]
    assert failed_msg.status == STATUS_FAILED
    assert failed_msg.error_category == ErrorCategory.PROVIDER_ERROR.value
    assert failed_msg.error == safe_error_display(ErrorCategory.PROVIDER_ERROR.value)

    # Open streaming rows from a previous session are demoted to interrupted.
    interrupted_msg = controller.messages[2]
    assert interrupted_msg.status == STATUS_INTERRUPTED
    assert interrupted_msg.content == "partial text"


# -- Finding 3: New Chat race conditions & lifecycle --------------------------


async def test_send_rejected_while_finalizing() -> None:
    persistence = FakePersistence([])
    persistence.finalize_gate = asyncio.Event()
    controller, service, state, _events = _build(persistence)

    rid = controller.send("first")
    await controller.wait_idle()
    state.apply_chat_stream_started(rid, "ollama", "m", "local")

    controller.new_chat()
    assert controller.finalizing is True
    assert controller.can_send("second") is False
    assert controller.send("second") is None
    assert len(service.starts) == 1

    persistence.finalize_gate.set()
    await controller.wait_idle()
    assert controller.finalizing is False


async def test_repeated_new_chat_while_finalizing_does_not_bypass_wait() -> None:
    persistence = FakePersistence([])
    persistence.finalize_gate = asyncio.Event()
    controller, _service, state, _events = _build(persistence)

    rid = controller.send("hello")
    await controller.wait_idle()
    state.apply_chat_stream_started(rid, "ollama", "m", "local")
    controller.new_chat()
    assert controller.finalizing is True
    state.apply_chat_stream_cancelled(rid, "partial")
    assert controller.active_request_id is None

    # Repeated new_chat must NOT reset while DB finalize is still in flight.
    controller.new_chat()
    assert len(controller.messages) == 2
    assert controller.finalizing is True

    persistence.finalize_gate.set()
    await controller.wait_idle()
    assert controller.messages == ()
    assert controller.finalizing is False


async def test_stale_reset_callback_does_not_clear_active_finalizing() -> None:
    persistence = FakePersistence([])
    controller, _service, _state, _events = _build(persistence)

    stale_task = asyncio.create_task(asyncio.sleep(0))
    await stale_task
    current_task = asyncio.create_task(asyncio.sleep(0.05))
    controller._finalizing = True  # noqa: SLF001
    controller._reset_task = current_task  # noqa: SLF001

    controller._on_reset_done(stale_task)  # noqa: SLF001
    assert controller.finalizing is True
    assert controller._reset_task is current_task  # noqa: SLF001

    await current_task
    controller._on_reset_done(current_task)  # noqa: SLF001
    assert controller.finalizing is False


async def test_stale_finalize_cannot_reset_newer_conversation() -> None:
    persistence = FakePersistence([])
    persistence.finalize_gate = asyncio.Event()
    controller, _service, state, _events = _build(persistence)

    rid = controller.send("old")
    await controller.wait_idle()
    state.apply_chat_stream_started(rid, "ollama", "m", "local")

    controller.new_chat()
    await asyncio.sleep(0)
    # Simulate another conversation having taken over before reset completes.
    controller._conversation_id = "9" * 32  # noqa: SLF001
    persistence.finalize_gate.set()
    await controller.wait_idle()

    assert controller.conversation_id == "9" * 32
    assert len(controller.messages) == 2


async def test_shutdown_drains_reconciliation_tasks() -> None:
    persistence = FakePersistence([])
    controller, _service, _state, _events = _build(persistence)

    await controller.shutdown()
    assert persistence.drain_calls >= 1


def test_finalize_confirmed_bookkeeping_is_bounded() -> None:
    persistence = FakePersistence([])
    controller, _service, _state, _events = _build(persistence)

    for idx in range(100):
        controller._record_finalize_confirmed(f"req-{idx}", True)  # noqa: SLF001

    assert len(controller._finalize_confirmed) <= 64  # noqa: SLF001
    assert controller._finalize_confirmed["req-99"] is True  # noqa: SLF001


# -- Stage 2.8 Gate A P0 regression tests ------------------------------------


async def test_new_chat_during_persist_initial_turn_finalizes_and_never_dispatches() -> (
    None
):
    persistence = FakePersistence([])
    persist_entered = asyncio.Event()
    persist_release = asyncio.Event()
    orig_persist = persistence.persist_initial_turn

    async def gated_persist(**kwargs):
        persist_entered.set()
        await persist_release.wait()
        return await orig_persist(**kwargs)

    persistence.persist_initial_turn = gated_persist  # type: ignore[method-assign]
    controller, service, _state, events = _build(persistence)

    controller.send("hello")
    await persist_entered.wait()

    # Trigger New Chat while persist_initial_turn is still in flight.
    controller.new_chat()
    assert controller.finalizing is True
    assert len(controller.messages) == 2  # UI must not reset before DB settles
    assert service.starts == []

    persist_release.set()
    await controller.wait_idle()

    assert "start" not in events
    assert service.starts == []
    assert controller.finalizing is False
    assert controller.messages == ()
    assert controller.conversation_id is None
    assert len(persistence.finals) == 1
    assert persistence.finals[0]["status"] == "cancelled"
    assert persistence.finals[0]["error"] == ErrorCategory.CANCELLED.value


async def test_new_chat_during_start_chat_waits_and_cancels_registration() -> None:
    persistence = FakePersistence([])
    controller, service, _state, _events = _build(persistence)
    start_entered = asyncio.Event()
    start_release = asyncio.Event()
    orig_start = service.start_chat

    async def gated_start(messages, *, provider=None, model=None, request_id=None):
        start_entered.set()
        await start_release.wait()
        return await orig_start(
            messages, provider=provider, model=model, request_id=request_id
        )

    service.start_chat = gated_start  # type: ignore[method-assign]

    rid = controller.send("hello")
    await start_entered.wait()

    controller.new_chat()
    assert controller.finalizing is True
    assert len(controller.messages) == 2
    assert rid in controller._cancel_intents  # noqa: SLF001

    start_release.set()
    await controller.wait_idle()

    assert service.starts == [rid]
    assert service.cancels == [rid]
    assert rid not in controller._cancel_intents  # noqa: SLF001
    assert controller.finalizing is False
    assert controller.messages == ()
    assert persistence.finals[-1]["status"] == "cancelled"


async def test_repeated_new_chat_after_failed_finalization_retries_and_preserves() -> (
    None
):
    persistence = FakePersistence([])
    persistence.finalize_result = False
    controller, _service, state, _events = _build(persistence)
    errors: list[str] = []
    controller.error_occurred.connect(errors.append)

    rid = controller.send("hello")
    await controller.wait_idle()
    state.apply_chat_stream_started(rid, "ollama", "m", "local")
    state.apply_chat_stream_completed(rid, "done", 3)
    await controller.wait_idle()

    # Stream completed, so _active_request_id is None, but finalization failed!
    assert controller.active_request_id is None
    assert len(controller.messages) == 2

    # First New Chat must attempt finalization, fail, and preserve conversation.
    controller.new_chat()
    await controller.wait_idle()
    assert len(controller.messages) == 2
    assert controller.conversation_id == "d" * 32
    assert controller.finalizing is False
    assert errors and "not switched" in errors[-1]

    # Second New Chat must NOT bypass finalization even though _active_request_id is None.
    controller.new_chat()
    await controller.wait_idle()
    assert len(controller.messages) == 2
    assert controller.conversation_id == "d" * 32

    # Once persistence recovers, a subsequent New Chat succeeds.
    persistence.finalize_result = True
    controller.new_chat()
    await controller.wait_idle()
    assert controller.messages == ()
    assert controller.conversation_id is None
    assert persistence.finals[-1]["status"] == "completed"


async def test_conversation_takeover_guard_handles_none_and_rejects_load() -> None:
    persistence = FakePersistence([])
    persistence.finalize_gate = asyncio.Event()
    controller, _service, state, _events = _build(persistence)

    # Case 1: draft conversation (conversation_id is None) taken over during reset.
    persist_entered = asyncio.Event()
    persist_release = asyncio.Event()
    orig_persist = persistence.persist_initial_turn

    async def gated_persist(**kwargs):
        persist_entered.set()
        await persist_release.wait()
        return await orig_persist(**kwargs)

    persistence.persist_initial_turn = gated_persist  # type: ignore[method-assign]
    controller.send("draft")
    await persist_entered.wait()
    assert controller.conversation_id is None

    controller.new_chat()
    # load_conversation must be rejected while finalization/reset is active.
    persistence.stored["9" * 32] = [make_stored_message(conversation_id="9" * 32)]
    assert await controller.load_conversation("9" * 32) is False

    # Simulate external takeover from None -> "9" * 32.
    controller._conversation_id = "9" * 32  # noqa: SLF001
    persist_release.set()
    persistence.finalize_gate.set()
    await controller.wait_idle()

    assert controller.conversation_id == "9" * 32
    assert len(controller.messages) == 2

    # Case 2: once idle, load_conversation succeeds.
    assert await controller.load_conversation("9" * 32) is True
    assert controller.conversation_id == "9" * 32
    assert len(controller.messages) == 1
    assert state is not None


async def test_shutdown_during_async_operations_leaves_no_pending_tasks() -> None:
    # 1. Shutdown during persist_initial_turn
    p1 = FakePersistence([])
    entered1 = asyncio.Event()

    async def slow_persist(**kwargs):
        entered1.set()
        await asyncio.Future()

    p1.persist_initial_turn = slow_persist  # type: ignore[method-assign]
    c1, _s1, _st1, _e1 = _build(p1)
    c1.send("one")
    await entered1.wait()
    await c1.shutdown()
    assert c1._dispatch_task is None  # noqa: SLF001
    assert c1.closing is True

    # 2. Shutdown during mark_streaming
    p2 = FakePersistence([])
    entered2 = asyncio.Event()

    async def slow_mark(turn):
        entered2.set()
        await asyncio.Future()

    p2.mark_streaming = slow_mark  # type: ignore[method-assign]
    c2, _s2, _st2, _e2 = _build(p2)
    c2.send("two")
    await entered2.wait()
    await c2.shutdown()
    assert c2._dispatch_task is None  # noqa: SLF001

    # 3. Shutdown during start_chat
    p3 = FakePersistence([])
    c3, s3, _st3, _e3 = _build(p3)
    entered3 = asyncio.Event()

    async def slow_start(messages, *, provider=None, model=None, request_id=None):
        entered3.set()
        await asyncio.Future()

    s3.start_chat = slow_start  # type: ignore[method-assign]
    c3.send("three")
    await entered3.wait()
    await c3.shutdown()
    assert c3._dispatch_task is None  # noqa: SLF001
    assert len(s3.cancels) == 1

    # 4. Shutdown during finalize
    p4 = FakePersistence([])
    p4.finalize_gate = asyncio.Event()
    c4, _s4, st4, _e4 = _build(p4)
    rid = c4.send("four")
    await c4.wait_idle()
    st4.apply_chat_stream_started(rid, "ollama", "m", "local")
    st4.apply_chat_stream_completed(rid, "ok", 1)
    p4.finalize_gate.set()
    await c4.shutdown()
    assert not c4._finalize_tasks  # noqa: SLF001


# -- Stage 2.8 Gate B tests ---------------------------------------------------


async def test_explicit_retry_creates_assistant_only_turn_without_duplicating_user() -> None:
    persistence = FakePersistence([])
    controller, service, state, events = _build(persistence)

    rid = controller.send("explain gravity")
    await controller.wait_idle()
    state.apply_chat_stream_started(rid, "ollama", "m", "local")
    state.apply_chat_stream_failed(rid, "boom", "")
    await controller.wait_idle()

    failed_msg = controller.messages[-1]
    assert controller.can_retry(failed_msg.id) is True

    retry_rid = controller.retry(failed_msg.id)
    assert retry_rid is not None and retry_rid != rid
    await controller.wait_idle()

    assert "persist_retry" in events
    assert service.starts == [rid, retry_rid]
    assert [m.role for m in controller.messages] == [
        "user",
        "assistant",
        "assistant",
    ]
    state.apply_chat_stream_started(retry_rid, "ollama", "m", "local")
    state.apply_chat_stream_completed(retry_rid, "Gravity curves spacetime.", 5)
    await controller.wait_idle()
    assert controller.messages[-1].content == "Gravity curves spacetime."


async def test_switch_rename_and_delete_conversation_respects_busy_guards() -> None:
    from datetime import datetime
    from jarvis.conversations.models import ConversationSummary

    persistence = FakePersistence([])
    persistence.summaries = [  # type: ignore[attr-defined]
        ConversationSummary(
            id="a" * 32,
            title="Alpha",
            mode="workspace",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            message_count=2,
        ),
        ConversationSummary(
            id="b" * 32,
            title="Beta",
            mode="workspace",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            message_count=1,
        ),
    ]
    persistence.stored["a" * 32] = [
        make_stored_message(conversation_id="a" * 32, role="user", content="hi a")
    ]
    persistence.stored["b" * 32] = [
        make_stored_message(conversation_id="b" * 32, role="user", content="hi b")
    ]
    controller, _service, _state, _events = _build(persistence)

    await controller.refresh_conversations()
    assert len(controller.conversations_list) == 2

    assert await controller.switch_conversation("a" * 32) is True
    assert controller.conversation_id == "a" * 32
    assert persistence.active_id == "a" * 32

    assert await controller.rename_conversation("a" * 32, "Alpha Renamed") is True
    assert controller.conversations_list[0].title == "Alpha Renamed"

    assert await controller.delete_conversation("a" * 32) is True
    assert controller.conversation_id is None
    assert controller.messages == ()
    assert len(controller.conversations_list) == 1


async def test_model_selection_override_and_db_recovery() -> None:
    persistence = FakePersistence([])
    persistence.stored["c" * 32] = [
        make_stored_message(conversation_id="c" * 32, role="user", content="saved")
    ]
    service = FakeChatServiceWithProvider(persistence.events)
    state = AppState()
    controller = ChatController(
        state,
        service,
        None,
        conversations=persistence,
        install_consent=False,
    )

    controller.set_model_selection("groq", "llama-3.3-70b-versatile")
    rid = controller.send("hello cloud")
    await controller.wait_idle()
    assert service.start_calls[-1] == {
        "provider": "groq",
        "model": "llama-3.3-70b-versatile",
        "request_id": rid,
    }
    state.apply_chat_stream_completed(rid, "ok", 1)
    await controller.wait_idle()

    # Simulate DB outage -> mark unavailable -> recover via retry_persistence_connection.
    controller.mark_persistence_unavailable()
    assert controller.persistence_unavailable is True

    async def fail_lookup():
        raise RuntimeError("still down")

    orig_lookup = persistence.get_active_conversation_id
    persistence.get_active_conversation_id = fail_lookup  # type: ignore[method-assign]
    assert await controller.retry_persistence_connection() is False
    assert controller.persistence_unavailable is True

    persistence.get_active_conversation_id = orig_lookup  # type: ignore[method-assign]
    assert await controller.retry_persistence_connection() is True
    assert controller.persistence_unavailable is False
