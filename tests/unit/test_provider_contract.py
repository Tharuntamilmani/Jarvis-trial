"""Provider interface contract tests (Stage 2.1).

These rules bind EVERY implementing provider — real (Stage 2.2/2.3) and
future. A `FakeProvider` stands in for a correct implementation; the
contract assertions run against it.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest

from jarvis.core.event_bus import EventBus
from jarvis.core.events import ChatStreamCancelled, ChatToken
from jarvis.intelligence.providers.base import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    LLMProvider,
    ModelInfo,
    ProviderCapabilities,
    ProviderHealth,
    PrivacyLevel,
    StreamCancelled,
    StreamDone,
    StreamError,
    StreamStart,
    Token,
)
from jarvis.intelligence.providers.errors import (
    MAX_RETRY_AFTER_SECONDS,
    MIN_RETRY_AFTER_SECONDS,
    ProviderAuthError,
    ProviderError,
    ProviderInvalidRequest,
    ProviderRateLimited,
    normalize_retry_after,
)


class FakeProvider(LLMProvider):
    """A correct minimal implementation used to pin the contract."""

    id = "fake"

    def __init__(self) -> None:
        self.capabilities = ProviderCapabilities(
            streaming=True,
            tools=False,
            context_size=4096,
            privacy=PrivacyLevel.LOCAL,
        )
        self.streams_closed = 0

    async def health(self) -> ProviderHealth:
        return ProviderHealth(available=True)

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="fake-mini", name="Fake Mini")]

    async def chat(self, request: ChatRequest) -> ChatResponse:
        assert request.request_id, "providers receive non-empty request_id"
        return ChatResponse(
            content="hello",
            model=request.model or "fake-mini",
            provider=self.id,
            tokens_used=2,
        )

    async def _stream_impl(
        self, request: ChatRequest
    ) -> AsyncIterator[object]:  # pragma: no cover - exercised via stream()
        yield StreamStart(request_id=request.request_id, model="fake-mini")
        for seq, word in enumerate(["hello", " ", "world"]):
            yield Token(request_id=request.request_id, seq=seq, text=word)
            await asyncio.sleep(0.01)
        yield StreamDone(request_id=request.request_id, content="hello world", tokens_used=3)

    def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:  # noqa: F821
        assert request.request_id, "providers receive non-empty request_id"
        return self._stream_impl(request)


# -- Basic contract --------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_returns_typed_response() -> None:
    provider = FakeProvider()
    request = ChatRequest(
        model=None,
        messages=[ChatMessage("user", "hi")],
        request_id="req-1",
    )
    response = await provider.chat(request)
    assert isinstance(response, ChatResponse)
    assert response.provider == "fake"
    assert response.finish_reason == "stop"


@pytest.mark.asyncio
async def test_stream_shape_start_tokens_single_terminal() -> None:
    provider = FakeProvider()
    request = ChatRequest(model=None, messages=[], request_id="req-2")
    events: list[object] = []
    async for event in provider.stream(request):
        events.append(event)

    assert isinstance(events[0], StreamStart)
    assert events[0].request_id == "req-2"
    tokens = [e for e in events if isinstance(e, Token)]
    assert [t.seq for t in tokens] == [0, 1, 2]  # monotonic
    terminal = events[-1]
    assert isinstance(terminal, StreamDone)
    # Exactly one terminal event in the whole stream.
    assert sum(
        isinstance(e, (StreamDone, StreamError, StreamCancelled)) for e in events
    ) == 1


@pytest.mark.asyncio
async def test_providers_may_assume_non_empty_request_id() -> None:
    provider = FakeProvider()
    with pytest.raises(AssertionError):
        await provider.chat(
            ChatRequest(model=None, messages=[], request_id="")
        )


# -- Cancellation contract ---------------------------------------------------------


@pytest.mark.asyncio
async def test_cancellation_propagates_and_closes_stream() -> None:
    """Cancellation is NEVER converted into StreamError."""

    class SlowStreamingProvider(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.streams_closed = 0

        async def _stream_impl(self, request: ChatRequest) -> AsyncIterator[object]:
            try:
                yield StreamStart(request_id=request.request_id, model="fake-mini")
                for seq in range(100):
                    yield Token(request_id=request.request_id, seq=seq, text="x")
                    await asyncio.sleep(0.01)
                yield StreamDone(request_id=request.request_id, content="never")
            finally:
                # Providers close their HTTP stream here (fake aclose()).
                self.streams_closed += 1

    provider = SlowStreamingProvider()
    request = ChatRequest(model=None, messages=[], request_id="req-3")
    events: list[object] = []
    gen = provider.stream(request)

    async def consume() -> None:
        try:
            async for event in gen:
                events.append(event)
                await asyncio.sleep(0)  # yield to the loop so cancel can land
        finally:
            # ChatService (Stage 2.5) MUST aclose() provider streams on
            # cancellation — this is what closes the provider's HTTP
            # response and runs its cleanup.
            await gen.aclose()

    task = asyncio.ensure_future(consume())
    await asyncio.sleep(0.03)  # let a few tokens flow
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert provider.streams_closed == 1           # HTTP stream closed
    assert not any(isinstance(e, StreamError) for e in events)
    assert not any(isinstance(e, StreamDone) for e in events)
    assert any(isinstance(e, Token) for e in events)  # partial content flowed


# -- Error taxonomy contract ---------------------------------------------------------


def test_transient_errors_share_base() -> None:
    from jarvis.intelligence.providers.errors import (
        ProviderTimeout,
        ProviderUnavailable,
    )

    for exc in (ProviderTimeout("t"), ProviderUnavailable("u")):
        assert isinstance(exc, ProviderError)


def test_auth_and_invalid_request_are_non_retryable_types() -> None:
    assert issubclass(ProviderAuthError, ProviderError)
    assert issubclass(ProviderInvalidRequest, ProviderError)


def test_rate_limited_carries_bounded_retry_after() -> None:
    exc = ProviderRateLimited("slow down", retry_after=120)
    assert exc.retry_after == MAX_RETRY_AFTER_SECONDS  # clamped down
    assert ProviderRateLimited("x", retry_after=0.2).retry_after == MIN_RETRY_AFTER_SECONDS
    assert ProviderRateLimited("x").retry_after == 2.0  # default


def test_normalize_retry_after() -> None:
    assert normalize_retry_after(None) == 2.0
    assert normalize_retry_after(0.1) == 1.0
    assert normalize_retry_after(5) == 5.0
    assert normalize_retry_after(999) == MAX_RETRY_AFTER_SECONDS


# -- Bus events for chat streaming (Stage 2.1 additions) ------------------------------


@pytest.mark.asyncio
async def test_chat_events_subscribe_by_class() -> None:
    bus = EventBus()
    tokens: list[ChatToken] = []
    cancelled: list[ChatStreamCancelled] = []
    bus.subscribe(ChatToken, tokens.append)
    bus.subscribe(ChatStreamCancelled, cancelled.append)
    await bus.publish(ChatToken(request_id="r", seq=0, text="hi"))
    await bus.publish(ChatStreamCancelled(request_id="r", partial_content="hi"))
    assert len(tokens) == 1 and tokens[0].text == "hi"
    assert len(cancelled) == 1 and cancelled[0].partial_content == "hi"
