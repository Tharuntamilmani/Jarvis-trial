"""ChatController behaviour: requests, sequencing, cancel, consent, status."""

from __future__ import annotations

import asyncio

from jarvis.core.engine import ProviderStatus
from jarvis.core.event_bus import EventBus
from jarvis.core.events import CloudFallbackProposed
from jarvis.desktop.viewmodels.app_state import AppState
from jarvis.desktop.viewmodels.chat_controller import ChatController
from jarvis.desktop.viewmodels.chat_models import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    ConversationMessage,
)
from jarvis.intelligence.chat_service import CancelOutcome
from jarvis.intelligence.cloud_payload import build_cloud_payload
from jarvis.intelligence.consent import ConsentManager
from jarvis.intelligence.prompts import JARVIS_SYSTEM_PROMPT
from jarvis.intelligence.providers.base import ChatMessage, ChatRequest
from tests.ui.chat_test_helpers import StubConversationPersistence


class FakeChatService:
    def __init__(self) -> None:
        self.starts: list[dict] = []
        self.cancels: list[str] = []
        self.gate: asyncio.Event | None = None

    async def start_chat(
        self,
        messages,
        *,
        provider=None,
        model=None,
        request_id=None,
    ) -> str:
        self.starts.append(
            {
                "messages": list(messages),
                "provider": provider,
                "model": model,
                "request_id": request_id,
            }
        )
        if self.gate is not None:
            await self.gate.wait()
        assert request_id
        return request_id

    async def cancel(self, request_id: str) -> CancelOutcome:
        self.cancels.append(request_id)
        return CancelOutcome.CANCELLED


class FakeProviderSource:
    def __init__(self, statuses: dict[str, ProviderStatus]) -> None:
        self.statuses = statuses
        self.snapshot_calls = 0
        self.refresh_calls = 0
        self.fail = False

    def provider_status_snapshot(self):
        self.snapshot_calls += 1
        return dict(self.statuses)

    async def refresh_provider_statuses(self):
        self.refresh_calls += 1
        if self.fail:
            raise RuntimeError("internal secret detail")
        return dict(self.statuses)


def _statuses() -> dict[str, ProviderStatus]:
    return {
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


def _controller(
    *,
    service: FakeChatService | None = None,
    consent: ConsentManager | None = None,
    source: FakeProviderSource | None = None,
    conversations=None,
    request_id_factory=None,
    clock=None,
) -> ChatController:
    state = AppState()
    return ChatController(
        state,
        service or FakeChatService(),
        consent,
        provider_source=source,
        conversations=conversations or StubConversationPersistence(),
        request_id_factory=request_id_factory,
        clock=clock,
    )


async def _until(predicate, timeout: float = 1.0) -> None:
    for _ in range(int(timeout / 0.005)):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition was not met")


# -- Sending / history ---------------------------------------------------------


async def test_send_creates_one_user_and_one_assistant_message() -> None:
    service = FakeChatService()
    controller = _controller(
        service=service, request_id_factory=lambda: "request-1"
    )

    request_id = controller.send("  hello JARVIS  ")
    assert request_id == "request-1"
    assert controller.busy is True
    assert [m.role for m in controller.messages] == ["user", "assistant"]
    assert controller.messages[0].content == "hello JARVIS"
    assert controller.messages[1].status == STATUS_PENDING

    await controller.wait_idle()
    assert len(service.starts) == 1
    assert service.starts[0]["request_id"] == "request-1"
    assert service.starts[0]["messages"] == [
        ChatMessage(role="user", content="hello JARVIS")
    ]
    assert controller.messages[0].status == STATUS_COMPLETED


async def test_whitespace_only_and_duplicate_sends_are_rejected() -> None:
    service = FakeChatService()
    controller = _controller(service=service)
    assert controller.send("   ") is None
    assert controller.send("first") is not None
    assert controller.send("second") is None
    await controller.wait_idle()
    assert len(service.starts) == 1


async def test_send_fails_safely_when_service_is_unavailable() -> None:
    controller = ChatController(
        AppState(),
        None,
        conversations=StubConversationPersistence(),
    )
    errors: list[str] = []
    controller.error_occurred.connect(errors.append)
    assert controller.send("hello") is None
    assert errors == ["Chat service is unavailable."]


# -- Streaming -----------------------------------------------------------------


async def test_streaming_sequence_dedup_and_final_content() -> None:
    service = FakeChatService()
    controller = _controller(service=service)
    state = controller._state  # noqa: SLF001 - test observes shared state

    updates: list[ConversationMessage] = []
    controller.message_updated.connect(updates.append)

    rid = controller.send("hi")
    await controller.wait_idle()
    state.apply_chat_stream_started(rid, "ollama", "qwen3:8b", "local")
    assert controller.active_provider == "ollama"
    assert controller.active_privacy == "local"

    state.apply_chat_token(rid, 0, "Hel")
    state.apply_chat_token(rid, 0, "DUPLICATE")
    state.apply_chat_token(rid, 2, "SKIPPED")
    state.apply_chat_token(rid, 1, "lo")
    assert controller.messages[-1].content == "Hello"

    state.apply_chat_stream_completed(rid, "Hello!", 2)
    message = controller.messages[-1]
    assert message.content == "Hello!"
    assert message.status == STATUS_COMPLETED
    assert message.tokens_used == 2
    assert controller.busy is False

    # Late events after the terminal state are ignored.
    state.apply_chat_token(rid, 2, "late")
    state.apply_chat_stream_failed(rid, "late failure", "late")
    assert controller.messages[-1].content == "Hello!"
    assert controller.messages[-1].status == STATUS_COMPLETED
    assert updates


async def test_events_for_other_requests_are_filtered() -> None:
    controller = _controller()
    state = controller._state  # noqa: SLF001
    rid = controller.send("hi")
    await controller.wait_idle()

    state.apply_chat_token("other-request", 0, "wrong")
    assert controller.messages[-1].content == ""

    state.apply_chat_token(rid, 0, "right")
    assert controller.messages[-1].content == "right"


async def test_token_repaint_is_coalesced_but_content_is_complete() -> None:
    ticks = iter([0.0, 0.01, 0.02, 0.03, 0.1])
    controller = _controller(clock=lambda: next(ticks))
    state = controller._state  # noqa: SLF001
    updates: list[ConversationMessage] = []
    controller.message_updated.connect(updates.append)

    rid = controller.send("hi")
    await controller.wait_idle()
    before = len(updates)
    state.apply_chat_token(rid, 0, "a")
    state.apply_chat_token(rid, 1, "b")
    state.apply_chat_token(rid, 2, "c")
    state.apply_chat_token(rid, 3, "d")

    assert controller.messages[-1].content == "abcd"
    # First token flushes; the next two are coalesced; the last flushes.
    assert len(updates) - before == 2


# -- Terminal states ------------------------------------------------------------


async def test_failed_state_preserves_partial_content() -> None:
    controller = _controller()
    state = controller._state  # noqa: SLF001
    rid = controller.send("hi")
    await controller.wait_idle()
    state.apply_chat_token(rid, 0, "partial")
    state.apply_chat_stream_failed(rid, "provider failed", "partial answer")

    message = controller.messages[-1]
    assert message.content == "partial answer"
    assert message.status == STATUS_FAILED
    assert message.error == "provider failed"
    assert controller.busy is False


async def test_cancelled_state_preserves_partial_content() -> None:
    controller = _controller()
    state = controller._state  # noqa: SLF001
    rid = controller.send("hi")
    await controller.wait_idle()
    state.apply_chat_token(rid, 0, "half")
    state.apply_chat_stream_cancelled(rid, "half an answer")

    message = controller.messages[-1]
    assert message.content == "half an answer"
    assert message.status == STATUS_CANCELLED
    assert controller.busy is False


# -- Stop / cancellation --------------------------------------------------------


async def test_stop_calls_cancel_once_with_the_active_request_id() -> None:
    service = FakeChatService()
    controller = _controller(service=service)
    state = controller._state  # noqa: SLF001
    rid = controller.send("hi")
    await controller.wait_idle()

    assert controller.stop() is True
    assert controller.stop() is True
    await controller.wait_idle()
    assert service.cancels == [rid]

    state.apply_chat_stream_cancelled(rid, "")
    assert controller.stop() is False


async def test_stop_before_start_chat_returns_cancels_exactly_once() -> None:
    service = FakeChatService()
    service.gate = asyncio.Event()
    controller = _controller(service=service)
    rid = controller.send("hi")
    await asyncio.sleep(0)  # dispatch has started and is awaiting the gate

    assert controller.stop() is True
    assert service.cancels == []  # not registered yet
    service.gate.set()
    await controller.wait_idle()

    assert service.cancels == [rid]
    assert rid not in controller._cancel_intents  # noqa: SLF001


async def test_stop_denies_pending_consent() -> None:
    service = FakeChatService()
    consent = ConsentManager(_consent_settings(), EventBus())
    controller = _controller(service=service, consent=consent)
    rid = controller.send("hi")
    await controller.wait_idle()

    payload, proposal = _consent_payload(rid, "hi")
    task = asyncio.create_task(
        consent.request_consent(
            request_id=rid, proposal=proposal, payload=payload
        )
    )
    await _until(lambda: controller.current_consent is not None)

    controller.stop()
    decision = await task
    assert decision.granted is False
    await _until(lambda: controller.current_consent is None)


# -- Consent --------------------------------------------------------------------


def _consent_settings():
    from tests.unit.chat_helpers import chat_settings
    from pathlib import Path
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="jarvis-consent-"))
    return chat_settings(root)


def _consent_payload(rid: str, user_message: str):
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
        reason="local unavailable",
        provider="groq",
        model="",
        payload_preview=payload.preview(),
        payload_fingerprint=payload.fingerprint,
        scope=payload.scope_description,
    )
    return payload, proposal


async def _pending_consent(controller, consent, rid, user_message="current"):
    payload, proposal = _consent_payload(rid, user_message)
    task = asyncio.create_task(
        consent.request_consent(
            request_id=rid, proposal=proposal, payload=payload
        )
    )
    await _until(lambda: controller.current_consent is not None)
    return payload, task


async def test_consent_card_shows_exact_payload_and_only_current_turn() -> None:
    service = FakeChatService()
    consent = ConsentManager(_consent_settings(), EventBus())
    controller = _controller(service=service, consent=consent)

    # Seed a prior exchange so the conversation has history.
    first = controller.send("private first question")
    await controller.wait_idle()
    controller._state.apply_chat_stream_completed(  # noqa: SLF001
        first, "private first answer", 4
    )

    rid = controller.send("current question")
    await controller.wait_idle()
    payload, task = await _pending_consent(
        controller, consent, rid, "current question"
    )

    request = controller.current_consent
    assert request is not None
    assert request.system_prompt == JARVIS_SYSTEM_PROMPT
    assert request.user_message == "current question"
    assert request.provider == "groq"
    assert request.fingerprint == payload.fingerprint
    assert "private first question" not in request.system_prompt
    assert "private first answer" not in request.user_message

    assert controller.approve_consent(rid, payload.fingerprint) is True
    decision = await task
    assert decision.granted is True
    await _until(lambda: controller.current_consent is None)


async def test_consent_rejects_mismatched_request_and_fingerprint() -> None:
    service = FakeChatService()
    consent = ConsentManager(_consent_settings(), EventBus())
    controller = _controller(service=service, consent=consent)
    rid = controller.send("current")
    await controller.wait_idle()
    payload, task = await _pending_consent(controller, consent, rid, "current")

    assert controller.approve_consent("other-request") is False
    assert controller.approve_consent(rid, "wrong-fingerprint") is False
    assert controller.deny_consent(rid, payload.fingerprint) is True
    decision = await task
    assert decision.granted is False


async def test_consent_rejection_denies() -> None:
    service = FakeChatService()
    consent = ConsentManager(_consent_settings(), EventBus())
    controller = _controller(service=service, consent=consent)
    rid = controller.send("current")
    await controller.wait_idle()
    payload, task = await _pending_consent(controller, consent, rid, "current")

    assert controller.deny_consent(rid, payload.fingerprint) is True
    assert (await task).granted is False


async def test_shutdown_resolves_pending_consent_and_rejects_late_approval() -> None:
    service = FakeChatService()
    consent = ConsentManager(_consent_settings(), EventBus())
    controller = _controller(service=service, consent=consent)
    rid = controller.send("current")
    await controller.wait_idle()
    payload, task = await _pending_consent(controller, consent, rid, "current")

    await controller.shutdown()

    decision = await task
    assert decision.granted is False
    assert controller.current_consent is None
    assert controller.approve_consent(rid, payload.fingerprint) is False
    assert controller.busy is False
    assert service.cancels == [rid]


# -- Provider status -------------------------------------------------------------


async def test_provider_status_refresh_is_one_shot_and_manual() -> None:
    source = FakeProviderSource(_statuses())
    controller = _controller(source=source)
    assert controller.provider_statuses["ollama"].model == "qwen3:8b"

    controller.on_workspace_shown()
    await controller.wait_provider_refresh()
    assert source.refresh_calls == 1

    controller.on_workspace_shown()  # hide/show again: no extra refresh
    await controller.wait_provider_refresh()
    assert source.refresh_calls == 1

    controller.refresh_provider_status()
    await controller.wait_provider_refresh()
    assert source.refresh_calls == 2

    await asyncio.sleep(0.05)
    assert source.refresh_calls == 2  # no polling timer


async def test_provider_status_health_failure_is_safe() -> None:
    source = FakeProviderSource(_statuses())
    controller = _controller(source=source)
    source.fail = True

    controller.refresh_provider_status()
    await controller.wait_provider_refresh()

    assert controller.provider_statuses["ollama"].detail == "connected"
    assert "secret" not in str(controller.provider_statuses)


async def test_provider_health_event_updates_status() -> None:
    source = FakeProviderSource(_statuses())
    controller = _controller(source=source)
    state = controller._state  # noqa: SLF001

    state.apply_provider_health("ollama", False, "connection refused")
    status = controller.provider_statuses["ollama"]
    assert status.available is False
    assert status.detail == "connection refused"
    assert status.model == "qwen3:8b"


# -- Conversation lifecycle -------------------------------------------------------


async def test_new_chat_resets_in_memory_state_and_cancels_active() -> None:
    service = FakeChatService()
    controller = _controller(service=service)
    state = controller._state  # noqa: SLF001
    rid = controller.send("hello")
    await controller.wait_idle()
    state.apply_chat_stream_started(rid, "ollama", "qwen3:8b", "local")

    controller.new_chat()
    await controller.wait_idle()

    assert controller.messages == ()
    assert controller.busy is False
    assert controller.active_provider == ""
    assert rid in service.cancels


async def test_completed_conversation_is_retained_for_reopen() -> None:
    controller = _controller()
    state = controller._state  # noqa: SLF001
    rid = controller.send("hello")
    await controller.wait_idle()
    state.apply_chat_stream_completed(rid, "hi there", 2)

    assert [m.content for m in controller.messages] == ["hello", "hi there"]


async def test_shutdown_cancels_request_while_dispatch_is_pending() -> None:
    service = FakeChatService()
    service.gate = asyncio.Event()
    controller = _controller(
        service=service, request_id_factory=lambda: "pending-request"
    )

    rid = controller.send("hello")
    await asyncio.sleep(0)  # dispatch started and is awaiting the gate
    assert controller._dispatch_task is not None  # noqa: SLF001
    assert not controller._dispatch_task.done()  # noqa: SLF001

    await controller.shutdown()
    await controller.shutdown()  # idempotent

    assert service.cancels == [rid]
    assert controller.active_request_id is None
    assert controller.busy is False


async def test_shutdown_during_streaming_is_clean() -> None:
    service = FakeChatService()
    controller = _controller(service=service)
    state = controller._state  # noqa: SLF001
    rid = controller.send("hello")
    await controller.wait_idle()
    state.apply_chat_token(rid, 0, "partial")

    await controller.shutdown()

    assert controller.busy is False
    assert controller.active_request_id is None
    assert service.cancels == [rid]
    assert controller.messages[-1].content == "partial"
