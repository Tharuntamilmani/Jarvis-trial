"""ChatService tests: IDs, sequencing, events, state, concurrency."""

from __future__ import annotations

import asyncio

import pytest

from jarvis.core.event_bus import EventBus
from jarvis.core.events import (
    AssistantStateChanged,
    ChatStreamCompleted,
    ChatStreamFailed,
    ChatStreamStarted,
    ChatToken,
)
from jarvis.intelligence.chat_service import (
    AssistantState,
    CancelOutcome,
    ChatCapacityError,
    ChatRequestNotFoundError,
    ChatService,
    ChatServiceError,
    ChatStatus,
    DuplicateRequestIdError,
)
from jarvis.intelligence.consent import ConsentManager
from jarvis.intelligence.providers.base import (
    ChatRequest,
    PrivacyLevel,
    StreamDone,
    StreamStart,
    Token,
)
from jarvis.intelligence.providers.errors import (
    ProviderInvalidRequest,
    ProviderResponseError,
    ProviderUnavailable,
)
from jarvis.intelligence.providers.registry import ProviderRegistry
from jarvis.intelligence.router import LLMRouter
from tests.unit.chat_helpers import (
    USER_MESSAGE,
    FakeRouter,
    StreamingProvider,
    chat_settings,
    make_service,
    scripted,
    simple_script,
)


def _collect(bus: EventBus):
    started: list[ChatStreamStarted] = []
    tokens: list[ChatToken] = []
    completed: list[ChatStreamCompleted] = []
    failed: list[ChatStreamFailed] = []
    states: list[AssistantStateChanged] = []
    bus.subscribe(ChatStreamStarted, started.append)
    bus.subscribe(ChatToken, tokens.append)
    bus.subscribe(ChatStreamCompleted, completed.append)
    bus.subscribe(ChatStreamFailed, failed.append)
    bus.subscribe(AssistantStateChanged, states.append)
    return started, tokens, completed, failed, states


# -- Request IDs ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generated_request_id_is_non_empty_and_shared_by_every_event(tmp_path) -> None:
    settings = chat_settings(tmp_path)
    router = FakeRouter(simple_script(("a", "b")))
    service, bus = make_service(settings, router)
    started, tokens, completed, failed, _ = _collect(bus)

    rid = await service.start_chat([USER_MESSAGE])
    result = await service.wait(rid)

    assert rid
    assert len(rid) == 32 and all(c in "0123456789abcdef" for c in rid)
    assert result.status is ChatStatus.COMPLETED
    assert result.request_id == rid
    assert [event.request_id for event in started] == [rid]
    assert [event.request_id for event in tokens] == [rid, rid]
    assert [event.request_id for event in completed] == [rid]
    assert failed == []


@pytest.mark.asyncio
async def test_injected_request_id_is_honored(tmp_path) -> None:
    settings = chat_settings(tmp_path)
    router = FakeRouter(simple_script(("x",)))
    service, _ = make_service(settings, router)

    rid = await service.start_chat([USER_MESSAGE], request_id="explicit-id")
    result = await service.wait(rid)

    assert rid == "explicit-id"
    assert result.request_id == "explicit-id"
    assert router.calls[0].request_id == "explicit-id"


@pytest.mark.asyncio
async def test_generated_id_collision_is_retried(tmp_path) -> None:
    settings = chat_settings(tmp_path, chat_max_concurrent_generations=2)
    release = asyncio.Event()
    started = asyncio.Event()

    async def blocking(request: ChatRequest):
        yield StreamStart(request_id=request.request_id, model="fake-model")
        started.set()
        await release.wait()
        yield StreamDone(request_id=request.request_id, content="", tokens_used=0)

    ids = iter(["collision", "collision", "unique-id"])
    router = FakeRouter(blocking)
    service, _ = make_service(settings, router, id_factory=lambda: next(ids))

    first = await service.start_chat([USER_MESSAGE])
    await started.wait()
    second = await service.start_chat([USER_MESSAGE])

    assert first == "collision"
    assert second == "unique-id"

    release.set()
    await asyncio.gather(service.wait(first), service.wait(second))


@pytest.mark.asyncio
async def test_duplicate_active_request_id_is_rejected(tmp_path) -> None:
    settings = chat_settings(tmp_path, chat_max_concurrent_generations=2)
    release = asyncio.Event()

    async def blocking(request: ChatRequest):
        yield StreamStart(request_id=request.request_id, model="fake-model")
        await release.wait()
        yield StreamDone(request_id=request.request_id, content="", tokens_used=0)

    service, _ = make_service(settings, FakeRouter(blocking))
    rid = await service.start_chat([USER_MESSAGE], request_id="same")

    with pytest.raises(DuplicateRequestIdError):
        await service.start_chat([USER_MESSAGE], request_id="same")

    release.set()
    await service.wait(rid)


@pytest.mark.asyncio
async def test_invalid_request_is_rejected_before_dispatch(tmp_path) -> None:
    settings = chat_settings(tmp_path)
    router = FakeRouter(simple_script())
    service, _ = make_service(settings, router)

    with pytest.raises(ProviderInvalidRequest):
        await service.start_chat([])

    assert router.calls == []
    assert service.active_request_ids == ()


# -- Event order and sequencing ----------------------------------------------------


@pytest.mark.asyncio
async def test_event_order_public_sequence_and_final_content(tmp_path) -> None:
    settings = chat_settings(tmp_path)
    router = FakeRouter(simple_script(("one", "-", "two"), tokens_used=11))
    service, bus = make_service(settings, router)
    started, tokens, completed, failed, states = _collect(bus)

    rid = await service.start_chat([USER_MESSAGE])
    result = await service.wait(rid)

    assert [event.provider for event in started] == ["fake"]
    assert [event.model for event in started] == ["fake-model"]
    assert [event.seq for event in tokens] == [0, 1, 2]
    assert [event.text for event in tokens] == ["one", "-", "two"]
    assert len(completed) == 1
    assert completed[0].content == "one-two"
    assert completed[0].tokens_used == 11
    assert failed == []
    assert result.content == "one-two"
    assert result.tokens_used == 11
    assert [state.state for state in states] == ["thinking", "speaking", "idle"]
    assert service.assistant_state is AssistantState.IDLE


@pytest.mark.asyncio
async def test_duplicate_and_out_of_order_provider_sequences_are_dropped(tmp_path) -> None:
    settings = chat_settings(tmp_path)
    router = FakeRouter(
        scripted(
            [
                lambda request: StreamStart(
                    request_id=request.request_id, model="fake-model"
                ),
                lambda request: Token(
                    request_id=request.request_id, seq=0, text="a"
                ),
                lambda request: Token(
                    request_id=request.request_id, seq=0, text="DUP"
                ),
                lambda request: Token(
                    request_id=request.request_id, seq=2, text="GAP"
                ),
                lambda request: Token(
                    request_id=request.request_id, seq=1, text="b"
                ),
                lambda request: Token(
                    request_id=request.request_id, seq=3, text="LATE"
                ),
                lambda request: StreamDone(
                    request_id=request.request_id,
                    content="provider-content-is-ignored",
                    tokens_used=9,
                ),
            ]
        )
    )
    service, bus = make_service(settings, router)
    _, tokens, completed, _, _ = _collect(bus)

    rid = await service.start_chat([USER_MESSAGE])
    result = await service.wait(rid)

    assert [event.text for event in tokens] == ["a", "b"]
    assert [event.seq for event in tokens] == [0, 1]
    assert len(completed) == 1
    assert completed[0].content == "ab"
    assert result.content == "ab"


@pytest.mark.asyncio
async def test_empty_chunks_are_ignored_and_do_not_consume_sequence(tmp_path) -> None:
    settings = chat_settings(tmp_path)
    router = FakeRouter(
        scripted(
            [
                lambda request: StreamStart(
                    request_id=request.request_id, model="fake-model"
                ),
                lambda request: Token(request_id=request.request_id, seq=0, text=""),
                lambda request: Token(
                    request_id=request.request_id, seq=1, text="visible"
                ),
                lambda request: StreamDone(
                    request_id=request.request_id, content="visible", tokens_used=1
                ),
            ]
        )
    )
    service, bus = make_service(settings, router)
    _, tokens, completed, _, _ = _collect(bus)

    rid = await service.start_chat([USER_MESSAGE])
    await service.wait(rid)

    assert [(event.seq, event.text) for event in tokens] == [(0, "visible")]
    assert completed[0].content == "visible"


@pytest.mark.asyncio
async def test_mismatched_request_ids_are_ignored(tmp_path) -> None:
    settings = chat_settings(tmp_path)
    router = FakeRouter(
        scripted(
            [
                lambda request: StreamStart(
                    request_id=request.request_id, model="fake-model"
                ),
                lambda request: Token(
                    request_id=request.request_id, seq=0, text="kept"
                ),
                lambda _request: Token(
                    request_id="other-request", seq=1, text="foreign"
                ),
                lambda _request: StreamDone(
                    request_id="other-request", content="foreign", tokens_used=99
                ),
            ]
        )
    )
    service, bus = make_service(settings, router)
    _, tokens, completed, failed, _ = _collect(bus)

    rid = await service.start_chat([USER_MESSAGE])
    result = await service.wait(rid)

    assert [event.text for event in tokens] == ["kept"]
    assert completed == []
    assert len(failed) == 1
    assert failed[0].partial_content == "kept"
    assert result.status is ChatStatus.FAILED
    assert result.content == "kept"


@pytest.mark.asyncio
async def test_no_events_after_terminal_and_stream_is_closed(tmp_path) -> None:
    settings = chat_settings(tmp_path)
    closed = {"count": 0}

    async def script(request: ChatRequest):
        try:
            yield StreamStart(request_id=request.request_id, model="fake-model")
            yield Token(request_id=request.request_id, seq=0, text="done-text")
            yield StreamDone(
                request_id=request.request_id, content="done-text", tokens_used=1
            )
            # Protocol violation after the terminal event: must never surface.
            yield Token(request_id=request.request_id, seq=1, text="after")
            yield StreamDone(
                request_id=request.request_id, content="after", tokens_used=2
            )
        finally:
            closed["count"] += 1

    router = FakeRouter(script)
    service, bus = make_service(settings, router)
    _, tokens, completed, failed, _ = _collect(bus)

    rid = await service.start_chat([USER_MESSAGE])
    await service.wait(rid)

    assert [event.text for event in tokens] == ["done-text"]
    assert len(completed) == 1
    assert failed == []
    assert closed["count"] >= 1
    assert service.active_request_ids == ()


@pytest.mark.asyncio
async def test_stream_without_terminal_event_fails_with_partial(tmp_path) -> None:
    settings = chat_settings(tmp_path)
    router = FakeRouter(
        scripted(
            [
                lambda request: StreamStart(
                    request_id=request.request_id, model="fake-model"
                ),
                lambda request: Token(
                    request_id=request.request_id, seq=0, text="partial"
                ),
            ]
        )
    )
    service, bus = make_service(settings, router)
    _, _, completed, failed, _ = _collect(bus)

    rid = await service.start_chat([USER_MESSAGE])
    result = await service.wait(rid)

    assert completed == []
    assert len(failed) == 1
    assert failed[0].partial_content == "partial"
    assert result.status is ChatStatus.FAILED
    assert result.content == "partial"


@pytest.mark.asyncio
async def test_provider_error_after_start_preserves_partial_and_never_restarts(
    tmp_path,
) -> None:
    settings = chat_settings(tmp_path)

    async def script(request: ChatRequest):
        yield StreamStart(request_id=request.request_id, model="fake-model")
        yield Token(request_id=request.request_id, seq=0, text="part")
        raise ProviderUnavailable("socket reset")

    router = FakeRouter(script)
    service, bus = make_service(settings, router)
    started, _, completed, failed, _ = _collect(bus)

    rid = await service.start_chat([USER_MESSAGE])
    result = await service.wait(rid)

    assert len(started) == 1
    assert completed == []
    assert len(failed) == 1
    assert failed[0].partial_content == "part"
    assert failed[0].detail
    assert "socket reset" in failed[0].detail
    assert result.status is ChatStatus.FAILED
    assert len(router.calls) == 1  # never restarts the provider


@pytest.mark.asyncio
async def test_router_failure_before_stream_start_has_no_started_event(tmp_path) -> None:
    settings = chat_settings(tmp_path)
    router = FakeRouter(raise_before=ProviderResponseError("routing failed"))
    service, bus = make_service(settings, router)
    started, tokens, completed, failed, _ = _collect(bus)

    rid = await service.start_chat([USER_MESSAGE])
    result = await service.wait(rid)

    assert started == []
    assert tokens == []
    assert completed == []
    assert len(failed) == 1
    assert failed[0].detail == "routing failed"
    assert result.status is ChatStatus.FAILED
    assert result.content == ""


# -- Concurrency and state ---------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_policy_enforces_concurrency_limit(tmp_path) -> None:
    settings = chat_settings(tmp_path)
    release = asyncio.Event()
    started = asyncio.Event()

    async def blocking(request: ChatRequest):
        yield StreamStart(request_id=request.request_id, model="fake-model")
        started.set()
        await release.wait()
        yield StreamDone(request_id=request.request_id, content="", tokens_used=0)

    service, _ = make_service(settings, FakeRouter(blocking))
    rid = await service.start_chat([USER_MESSAGE])
    await started.wait()

    with pytest.raises(ChatCapacityError):
        await service.start_chat([USER_MESSAGE])

    await service.cancel(rid)
    release.set()
    await service.wait(rid)

    # Capacity is released after cancellation.
    release.set()
    new_id = await service.start_chat([USER_MESSAGE])
    assert await service.wait(new_id)


@pytest.mark.asyncio
async def test_queue_policy_does_not_dispatch_until_slot_is_free(tmp_path) -> None:
    settings = chat_settings(
        tmp_path, chat_concurrency_policy="queue", chat_max_concurrent_generations=1
    )
    release = asyncio.Event()
    first_started = asyncio.Event()

    async def blocking(request: ChatRequest):
        yield StreamStart(request_id=request.request_id, model="fake-model")
        first_started.set()
        await release.wait()
        yield StreamDone(request_id=request.request_id, content="", tokens_used=0)

    router = FakeRouter(blocking)
    service, bus = make_service(settings, router)
    started, _, _, _, _ = _collect(bus)

    first = await service.start_chat([USER_MESSAGE])
    await first_started.wait()
    second = await service.start_chat([USER_MESSAGE])

    assert service.active_request_ids == (first, second)
    assert len(router.calls) == 1  # queued request has no provider work
    assert service.assistant_state is AssistantState.THINKING
    assert [event.request_id for event in started] == [first]

    release.set()
    await service.wait(first)
    await service.wait(second)

    assert len(router.calls) == 2
    assert [event.request_id for event in started] == [first, second]
    assert service.assistant_state is AssistantState.IDLE


@pytest.mark.asyncio
async def test_assistant_state_aggregates_across_requests(tmp_path) -> None:
    settings = chat_settings(tmp_path, chat_max_concurrent_generations=2)
    release = asyncio.Event()
    two_tokens = asyncio.Event()
    seen = {"tokens": 0}

    async def blocking(request: ChatRequest):
        yield StreamStart(request_id=request.request_id, model="fake-model")
        yield Token(request_id=request.request_id, seq=0, text="x")
        seen["tokens"] += 1
        if seen["tokens"] == 2:
            two_tokens.set()
        await release.wait()
        yield Token(request_id=request.request_id, seq=1, text="y")
        yield StreamDone(
            request_id=request.request_id, content="xy", tokens_used=2
        )

    service, bus = make_service(settings, FakeRouter(blocking))
    _collect(bus)

    first = await service.start_chat([USER_MESSAGE])
    second = await service.start_chat([USER_MESSAGE])
    await asyncio.wait_for(two_tokens.wait(), timeout=2)

    assert service.assistant_state is AssistantState.SPEAKING

    await service.cancel(first)
    await service.wait(first)
    assert service.assistant_state is AssistantState.SPEAKING  # second still active

    release.set()
    await service.wait(second)
    assert service.assistant_state is AssistantState.IDLE


@pytest.mark.asyncio
async def test_cancel_outcomes_for_unknown_and_finished_requests(tmp_path) -> None:
    settings = chat_settings(tmp_path)
    service, _ = make_service(settings, FakeRouter(simple_script(("x",))))

    assert await service.cancel("missing") is CancelOutcome.NOT_FOUND

    rid = await service.start_chat([USER_MESSAGE])
    await service.wait(rid)
    assert await service.cancel(rid) is CancelOutcome.ALREADY_FINISHED


@pytest.mark.asyncio
async def test_completed_result_cache_is_bounded(tmp_path) -> None:
    settings = chat_settings(tmp_path, chat_completed_cache_size=2)
    service, _ = make_service(settings, FakeRouter(simple_script(("x",))))

    ids = []
    for _ in range(3):
        rid = await service.start_chat([USER_MESSAGE])
        await service.wait(rid)
        ids.append(rid)

    with pytest.raises(ChatRequestNotFoundError):
        await service.wait(ids[0])
    assert (await service.wait(ids[1])).request_id == ids[1]
    assert (await service.wait(ids[2])).request_id == ids[2]
    assert service.partial(ids[0]) == ""


@pytest.mark.asyncio
async def test_closed_service_rejects_new_requests(tmp_path) -> None:
    settings = chat_settings(tmp_path)
    service, _ = make_service(settings, FakeRouter(simple_script(("x",))))
    await service.close()

    with pytest.raises(ChatServiceError):
        await service.start_chat([USER_MESSAGE])


# -- Integration through the real router -------------------------------------------


@pytest.mark.asyncio
async def test_real_router_local_stream_end_to_end(tmp_path) -> None:
    settings = chat_settings(tmp_path)
    bus = EventBus()
    registry = ProviderRegistry(bus)
    provider = StreamingProvider(
        "ollama", PrivacyLevel.LOCAL, simple_script(("local", "-", "answer"))
    )
    registry.register(provider)
    router = LLMRouter(
        settings=settings,
        providers=registry,
        bus=bus,
        consent=ConsentManager(settings, bus),
    )
    service = ChatService(settings, bus, router)
    started, tokens, completed, failed, states = _collect(bus)

    rid = await service.start_chat([USER_MESSAGE])
    result = await service.wait(rid)

    assert result.status is ChatStatus.COMPLETED
    assert result.content == "local-answer"
    assert result.provider == "ollama"
    assert result.model == "fake-model"
    assert started[0].provider == "ollama"
    assert [event.seq for event in tokens] == [0, 1, 2]
    assert completed[0].content == "local-answer"
    assert failed == []
    assert [state.state for state in states] == ["thinking", "speaking", "idle"]
    assert provider.closed == 1
