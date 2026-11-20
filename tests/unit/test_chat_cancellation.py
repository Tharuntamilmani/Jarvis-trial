"""ChatService cancellation, kill-switch, close and consent-cancel tests."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from jarvis.core.event_bus import EventBus
from jarvis.core.events import (
    ChatStreamCancelled,
    ChatStreamCompleted,
    ChatStreamFailed,
    ChatStreamStarted,
    ChatToken,
    CloudFallbackProposed,
)
from jarvis.core.exceptions import KillSwitchActiveError
from jarvis.intelligence.chat_service import (
    AssistantState,
    CancelOutcome,
    ChatService,
    ChatStatus,
)
from jarvis.intelligence.consent import ConsentManager
from jarvis.intelligence.providers.base import (
    StreamDone,
    StreamStart,
    Token,
)
from jarvis.intelligence.providers.groq_provider import GroqProvider
from jarvis.intelligence.providers.registry import ProviderRegistry
from jarvis.intelligence.router import LLMRouter
from tests.unit.chat_helpers import (
    USER_MESSAGE,
    FakeLifecycle,
    FakeRouter,
    chat_settings,
    make_service,
    simple_script,
)


def _collect(bus: EventBus):
    started: list[ChatStreamStarted] = []
    tokens: list[ChatToken] = []
    completed: list[ChatStreamCompleted] = []
    failed: list[ChatStreamFailed] = []
    cancelled: list[ChatStreamCancelled] = []
    bus.subscribe(ChatStreamStarted, started.append)
    bus.subscribe(ChatToken, tokens.append)
    bus.subscribe(ChatStreamCompleted, completed.append)
    bus.subscribe(ChatStreamFailed, failed.append)
    bus.subscribe(ChatStreamCancelled, cancelled.append)
    return started, tokens, completed, failed, cancelled


async def _wait_until(predicate, *, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


def _slow_script(block_event: asyncio.Event, *, closed: dict | None = None):
    async def script(request):
        try:
            yield StreamStart(request_id=request.request_id, model="fake-model")
            yield Token(request_id=request.request_id, seq=0, text="a")
            yield Token(request_id=request.request_id, seq=1, text="b")
            await block_event.wait()
            yield Token(request_id=request.request_id, seq=2, text="c")
            yield StreamDone(
                request_id=request.request_id, content="abc", tokens_used=3
            )
        finally:
            if closed is not None:
                closed["count"] += 1

    return script


@pytest.mark.asyncio
async def test_cancel_preserves_partial_and_publishes_one_cancellation(tmp_path) -> None:
    settings = chat_settings(tmp_path)
    block = asyncio.Event()
    closed = {"count": 0}
    service, bus = make_service(settings, FakeRouter(_slow_script(block, closed=closed)))
    started, tokens, completed, failed, cancelled = _collect(bus)

    rid = await service.start_chat([USER_MESSAGE])
    await _wait_until(lambda: len(tokens) >= 2)

    outcome = await service.cancel(rid)
    result = await service.wait(rid)

    assert outcome is CancelOutcome.CANCELLED
    assert result.status is ChatStatus.CANCELLED
    assert result.content == "ab"
    assert service.partial(rid) == "ab"
    assert len(cancelled) == 1
    assert cancelled[0].request_id == rid
    assert cancelled[0].partial_content == "ab"
    assert completed == []
    assert failed == []
    assert len(started) == 1
    assert closed["count"] >= 1  # provider stream closed
    assert service.active_request_ids == ()
    assert service.assistant_state is AssistantState.IDLE


@pytest.mark.asyncio
async def test_cancel_only_targets_the_named_request(tmp_path) -> None:
    settings = chat_settings(tmp_path, chat_max_concurrent_generations=2)
    block = asyncio.Event()
    service, bus = make_service(
        settings,
        FakeRouter(
            scripts=[_slow_script(block), simple_script(("quick",))]
        ),
    )
    started, _, completed, failed, cancelled = _collect(bus)

    slow = await service.start_chat([USER_MESSAGE])
    quick = await service.start_chat([USER_MESSAGE])

    quick_result = await service.wait(quick)
    assert quick_result.status is ChatStatus.COMPLETED
    assert quick_result.content == "quick"

    await service.cancel(slow)
    slow_result = await service.wait(slow)

    assert slow_result.status is ChatStatus.CANCELLED
    assert slow_result.content == "ab"
    assert len(completed) == 1
    assert completed[0].request_id == quick
    assert failed == []
    assert [event.request_id for event in cancelled] == [slow]
    assert [event.request_id for event in started] == [slow, quick]


@pytest.mark.asyncio
async def test_cancel_queued_request_never_dispatches_it(tmp_path) -> None:
    settings = chat_settings(
        tmp_path, chat_concurrency_policy="queue", chat_max_concurrent_generations=1
    )
    block = asyncio.Event()
    first_started = asyncio.Event()

    async def first_script(request):
        yield StreamStart(request_id=request.request_id, model="fake-model")
        first_started.set()
        await block.wait()
        yield StreamDone(request_id=request.request_id, content="", tokens_used=0)

    router = FakeRouter(scripts=[first_script, simple_script(("never",))])
    service, bus = make_service(settings, router)
    _, _, completed, failed, cancelled = _collect(bus)

    first = await service.start_chat([USER_MESSAGE])
    await first_started.wait()
    second = await service.start_chat([USER_MESSAGE])

    assert len(router.calls) == 1
    assert await service.cancel(second) is CancelOutcome.CANCELLED
    second_result = await service.wait(second)

    assert second_result.status is ChatStatus.CANCELLED
    assert second_result.content == ""
    assert [event.request_id for event in cancelled] == [second]
    assert completed == []
    assert failed == []

    block.set()
    assert (await service.wait(first)).status is ChatStatus.COMPLETED
    assert len(router.calls) == 1  # queued request was never dispatched


@pytest.mark.asyncio
async def test_kill_switch_rejects_new_requests(tmp_path) -> None:
    settings = chat_settings(tmp_path)
    lifecycle = FakeLifecycle()
    lifecycle.kill_switch_active = True
    router = FakeRouter(simple_script(("x",)))
    service, _ = make_service(settings, router, lifecycle=lifecycle)

    with pytest.raises(KillSwitchActiveError):
        await service.start_chat([USER_MESSAGE])

    assert router.calls == []


@pytest.mark.asyncio
async def test_kill_switch_cancels_active_and_queued_requests(tmp_path) -> None:
    settings = chat_settings(
        tmp_path, chat_concurrency_policy="queue", chat_max_concurrent_generations=1
    )
    lifecycle = FakeLifecycle()
    block = asyncio.Event()
    tokens_sent = asyncio.Event()

    async def script(request):
        yield StreamStart(request_id=request.request_id, model="fake-model")
        yield Token(request_id=request.request_id, seq=0, text="partial")
        tokens_sent.set()
        await block.wait()
        yield StreamDone(request_id=request.request_id, content="never", tokens_used=1)

    router = FakeRouter(script)
    service, bus = make_service(settings, router, lifecycle=lifecycle)
    started, _, completed, failed, cancelled = _collect(bus)

    first = await service.start_chat([USER_MESSAGE])
    await tokens_sent.wait()
    second = await service.start_chat([USER_MESSAGE])
    assert service.assistant_state is AssistantState.SPEAKING

    await lifecycle.trip()

    first_result = await service.wait(first)
    second_result = await service.wait(second)

    assert lifecycle.kill_switch_active is True
    assert first_result.status is ChatStatus.CANCELLED
    assert first_result.content == "partial"
    assert second_result.status is ChatStatus.CANCELLED
    assert second_result.content == ""
    assert completed == []
    assert failed == []
    assert sorted(event.request_id for event in cancelled) == sorted([first, second])
    assert [event.request_id for event in started] == [first]
    assert service.active_request_ids == ()
    assert service.assistant_state is AssistantState.IDLE

    with pytest.raises(KillSwitchActiveError):
        await service.start_chat([USER_MESSAGE])


@pytest.mark.asyncio
async def test_close_cancels_resolves_and_is_idempotent(tmp_path) -> None:
    settings = chat_settings(
        tmp_path, chat_concurrency_policy="queue", chat_max_concurrent_generations=1
    )
    block = asyncio.Event()
    first_started = asyncio.Event()

    async def script(request):
        yield StreamStart(request_id=request.request_id, model="fake-model")
        yield Token(request_id=request.request_id, seq=0, text="kept")
        first_started.set()
        await block.wait()
        yield StreamDone(request_id=request.request_id, content="x", tokens_used=1)

    service, bus = make_service(settings, FakeRouter(script))
    _, _, completed, failed, cancelled = _collect(bus)

    first = await service.start_chat([USER_MESSAGE])
    await first_started.wait()
    second = await service.start_chat([USER_MESSAGE])

    await service.close()

    assert service.active_request_ids == ()
    assert service.assistant_state is AssistantState.IDLE
    assert (await service.wait(first)).status is ChatStatus.CANCELLED
    assert (await service.wait(second)).status is ChatStatus.CANCELLED
    assert len(cancelled) == 2
    assert completed == []
    assert failed == []

    await service.close()  # idempotent


@pytest.mark.asyncio
async def test_cancel_during_pending_consent_makes_zero_groq_requests(tmp_path) -> None:
    settings = chat_settings(tmp_path)
    captured: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    groq = GroqProvider(settings, transport=httpx.MockTransport(handler))
    groq.is_configured = lambda: True
    groq._auth_headers = lambda: {"Authorization": "Bearer test-key"}

    bus = EventBus()
    registry = ProviderRegistry(bus)
    registry.register(groq)
    consent = ConsentManager(settings, bus)
    consent_holder: dict[str, str] = {}

    async def responder(proposal, payload) -> bool:
        consent_holder["request_id"] = proposal.request_id
        consent_holder["fingerprint"] = payload.fingerprint
        await asyncio.sleep(30)  # decision never arrives before cancellation
        return True

    consent.set_responder(responder)
    router = LLMRouter(
        settings=settings,
        providers=registry,
        bus=bus,
        consent=consent,
    )
    service = ChatService(settings, bus, router)
    started, _, completed, failed, cancelled = _collect(bus)
    proposals: list[CloudFallbackProposed] = []
    bus.subscribe(CloudFallbackProposed, proposals.append)

    try:
        rid = await service.start_chat([USER_MESSAGE], provider="groq")
        await _wait_until(lambda: consent.pending_count == 1)
        await _wait_until(lambda: bool(consent_holder))

        assert proposals and proposals[0].request_id == rid
        assert await service.cancel(rid) is CancelOutcome.CANCELLED
        result = await service.wait(rid)

        # A late approval must not dispatch anything.
        assert consent.resolve(
            rid, True, consent_holder["fingerprint"]
        ) is False
        await asyncio.sleep(0)

        assert result.status is ChatStatus.CANCELLED
        assert result.content == ""
        assert started == []
        assert cancelled and len(cancelled) == 1
        assert completed == []
        assert failed == []
        assert captured == []  # zero Groq HTTP requests
        assert consent.pending_count == 0
    finally:
        await service.close()
        await groq.aclose()
