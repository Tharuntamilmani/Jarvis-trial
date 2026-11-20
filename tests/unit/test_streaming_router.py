"""Streaming router tests (Stage 2.5).

Covers local-first streaming, StreamRouted metadata, the stricter
pre-``StreamStart`` fallback commitment rule, consent gating for cloud
streaming, and validation of the actual serialized Groq HTTP body over
``httpx.MockTransport``. No test makes a live request.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Callable

import httpx
import pytest

from jarvis.config import Settings
from jarvis.core.event_bus import EventBus
from jarvis.core.events import (
    CloudFallbackProposed,
    ProviderSelected,
)
from jarvis.core.exceptions import KillSwitchActiveError
from jarvis.intelligence.consent import ConsentManager
from jarvis.intelligence.prompts import JARVIS_SYSTEM_PROMPT
from jarvis.intelligence.providers.base import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    LLMProvider,
    PrivacyLevel,
    ProviderCapabilities,
    ProviderHealth,
    StreamDone,
    StreamError,
    StreamStart,
    Token,
)
from jarvis.intelligence.providers.errors import (
    ProviderAuthError,
    ProviderInvalidRequest,
    ProviderResponseError,
    ProviderUnavailable,
)
from jarvis.intelligence.providers.groq_provider import GroqProvider
from jarvis.intelligence.providers.registry import ProviderRegistry
from jarvis.intelligence.router import (
    CloudConsentDeniedError,
    LLMRouter,
    StreamRouted,
)

CURRENT = "current question from the user"
HISTORY = "old conversation turn"
MEMORY = "private memory content"
TOOL_OUTPUT = "private tool output"
FILE_PATH = "C:\\Users\\Tharun\\private-file.txt"


def _settings(tmp_path, **overrides) -> Settings:
    defaults = dict(
        log_dir=tmp_path / "logs",
        routing_max_retries=1,
        routing_budget_seconds=30.0,
        routing_backoff_base_seconds=0.001,
        routing_backoff_max_seconds=0.002,
        health_hard_down_ttl_seconds=5.0,
        _env_file=None,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _request(request_id: str = "req-stream") -> ChatRequest:
    return ChatRequest(
        model=None,
        messages=[
            ChatMessage("system", "hidden local system context"),
            ChatMessage("user", "older question"),
            ChatMessage("assistant", HISTORY),
            ChatMessage("tool", TOOL_OUTPUT),
            ChatMessage("system", MEMORY),
            ChatMessage("user", f"question about {FILE_PATH}"),
            ChatMessage("user", CURRENT),
        ],
        request_id=request_id,
        temperature=0.3,
        max_tokens=64,
    )


class StreamFakeProvider(LLMProvider):
    """Provider with scripted streaming and close/call accounting."""

    def __init__(
        self,
        provider_id: str,
        privacy: PrivacyLevel,
        factory: Callable[[ChatRequest], AsyncIterator[object]],
        *,
        configured: bool = True,
    ) -> None:
        self.id = provider_id
        self.capabilities = ProviderCapabilities(
            streaming=True,
            tools=True,
            context_size=100_000,
            privacy=privacy,
        )
        self._factory = factory
        self._configured = configured
        self.stream_calls: list[ChatRequest] = []
        self.closed = 0

    async def health(self) -> ProviderHealth:
        return ProviderHealth(available=True, detail="connected")

    async def list_models(self):
        return []

    async def chat(self, request: ChatRequest) -> ChatResponse:
        raise AssertionError("streaming tests must never call chat()")

    async def _wrapped(self, request: ChatRequest) -> AsyncIterator[object]:
        try:
            async for event in self._factory(request):
                yield event
        finally:
            self.closed += 1

    def stream(self, request: ChatRequest) -> AsyncIterator[object]:
        self.stream_calls.append(request)
        return self._wrapped(request)

    def is_configured(self) -> bool:
        return self._configured


def _events(
    request_id: str,
    *chunks: str,
    tokens_used: int = 7,
    model: str = "fake-model",
) -> list[object]:
    events: list[object] = [StreamStart(request_id=request_id, model=model)]
    for seq, text in enumerate(chunks):
        events.append(Token(request_id=request_id, seq=seq, text=text))
    events.append(
        StreamDone(
            request_id=request_id,
            content="".join(chunks),
            tokens_used=tokens_used,
        )
    )
    return events


def _scripted(events: list[object]):
    async def factory(_request: ChatRequest) -> AsyncIterator[object]:
        for event in events:
            yield event

    return factory


def _raises(exc: Exception):
    async def factory(_request: ChatRequest) -> AsyncIterator[object]:
        if False:  # pragma: no cover - keeps this an async generator
            yield None
        raise exc

    return factory


def _harness(
    settings: Settings,
    providers: list[LLMProvider],
    *,
    responder=None,
    kill_switch=None,
    sleep=None,
):
    bus = EventBus()
    registry = ProviderRegistry(bus)
    for provider in providers:
        registry.register(provider)
    consent = ConsentManager(settings, bus)
    if responder is not None:
        consent.set_responder(responder)
    router = LLMRouter(
        settings=settings,
        providers=registry,
        bus=bus,
        consent=consent,
        kill_switch=kill_switch,
        **({"sleep": sleep} if sleep is not None else {}),
    )
    return router, bus, consent


async def _collect(router: LLMRouter, request: ChatRequest, **kwargs) -> list[object]:
    return [event async for event in router.stream_chat(request, **kwargs)]


async def _approve(_proposal, _payload) -> bool:
    return True


async def _deny(_proposal, _payload) -> bool:
    return False


def _local(factory, **kwargs) -> StreamFakeProvider:
    return StreamFakeProvider("ollama", PrivacyLevel.LOCAL, factory, **kwargs)


def _cloud(factory, **kwargs) -> StreamFakeProvider:
    return StreamFakeProvider("groq", PrivacyLevel.CLOUD, factory, **kwargs)


# -- Local streaming ------------------------------------------------------------


@pytest.mark.asyncio
async def test_local_stream_yields_routed_then_events_without_cloud(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local(_scripted(_events("req-stream", "hello", " world")))
    cloud = _cloud(_raises(AssertionError("cloud must not be used")))
    router, bus, _ = _harness(settings, [local, cloud], responder=_approve)
    selected: list[ProviderSelected] = []
    bus.subscribe(ProviderSelected, selected.append)

    events = await _collect(router, _request())

    assert isinstance(events[0], StreamRouted)
    assert events[0].request_id == "req-stream"
    assert events[0].provider == "ollama"
    assert events[0].model == "fake-model"
    assert events[0].privacy == "local"
    assert events[0].cloud_consent == ""
    assert isinstance(events[1], StreamStart)
    assert [e.text for e in events if isinstance(e, Token)] == ["hello", " world"]
    assert isinstance(events[-1], StreamDone)
    assert local.stream_calls[0].messages == _request().messages  # history allowed locally
    assert cloud.stream_calls == []
    assert [event.provider for event in selected] == ["ollama"]
    assert local.closed == 1


@pytest.mark.asyncio
async def test_stream_routed_metadata_and_provider_ids_match(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local(_scripted(_events("req-stream", "x")))
    router, _, _ = _harness(settings, [local, _cloud(_raises(AssertionError()))])

    events = await _collect(router, _request())
    routed = events[0]

    assert isinstance(routed, StreamRouted)
    assert routed.request_id == "req-stream"
    assert all(event.request_id == routed.request_id for event in events[1:])


@pytest.mark.asyncio
async def test_pre_start_transient_failure_is_retried_then_streams(tmp_path) -> None:
    settings = _settings(tmp_path)
    attempts = {"count": 0}
    good = _events("req-stream", "ok")

    async def factory(request: ChatRequest) -> AsyncIterator[object]:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise ProviderUnavailable("connection refused")
        for event in good:
            yield event

    local = _local(factory)
    router, _, _ = _harness(settings, [local])

    events = await _collect(router, _request())

    assert attempts["count"] == 2
    assert [e.text for e in events if isinstance(e, Token)] == ["ok"]
    assert isinstance(events[-1], StreamDone)


@pytest.mark.asyncio
async def test_stream_without_terminal_after_start_yields_stream_error(tmp_path) -> None:
    settings = _settings(tmp_path)

    async def factory(request: ChatRequest) -> AsyncIterator[object]:
        yield StreamStart(request_id=request.request_id, model="fake-model")
        yield Token(request_id=request.request_id, seq=0, text="partial")
        # Ends without a terminal event.

    local = _local(factory)
    router, _, _ = _harness(settings, [local])

    events = await _collect(router, _request())

    assert isinstance(events[-1], StreamError)
    assert isinstance(events[-1].error, ProviderResponseError)


@pytest.mark.asyncio
async def test_auth_error_is_not_retried_and_never_falls_back(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local(_raises(ProviderAuthError("bad key")))
    cloud = _cloud(_raises(AssertionError("no fallback after auth error")))
    router, _, _ = _harness(settings, [local, cloud], responder=_approve)

    with pytest.raises(ProviderAuthError):
        await _collect(router, _request())

    assert len(local.stream_calls) == 1
    assert cloud.stream_calls == []


@pytest.mark.asyncio
async def test_invalid_request_is_not_retried_and_never_falls_back(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local(_raises(ProviderInvalidRequest("bad model")))
    cloud = _cloud(_raises(AssertionError("no fallback after invalid request")))
    router, _, _ = _harness(settings, [local, cloud], responder=_approve)

    with pytest.raises(ProviderInvalidRequest):
        await _collect(router, _request())

    assert len(local.stream_calls) == 1
    assert cloud.stream_calls == []


@pytest.mark.asyncio
async def test_kill_switch_blocks_streaming_before_dispatch(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local(_scripted(_events("req-stream", "x")))

    class Switch:
        active = True

    router, _, _ = _harness(settings, [local], kill_switch=Switch())

    with pytest.raises(KillSwitchActiveError):
        await _collect(router, _request())

    assert local.stream_calls == []


# -- Commitment rule / fallback ---------------------------------------------------


@pytest.mark.asyncio
async def test_local_failure_before_start_falls_back_with_consent(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local(_raises(ProviderUnavailable("local down")))
    cloud = _cloud(_scripted(_events("req-stream", "cloud", model="cloud-model")))
    router, bus, consent = _harness(settings, [local, cloud], responder=_approve)
    proposals: list[CloudFallbackProposed] = []
    bus.subscribe(CloudFallbackProposed, proposals.append)

    events = await _collect(router, _request())

    routed = events[0]
    assert isinstance(routed, StreamRouted)
    assert routed.provider == "groq"
    assert routed.privacy == "cloud"
    assert routed.model == "cloud-model"
    assert routed.cloud_consent == "granted"
    assert len(proposals) == 1
    assert len(cloud.stream_calls) == 1
    assert len(cloud.stream_calls[0].messages) == 2
    assert [m.role for m in cloud.stream_calls[0].messages] == ["system", "user"]
    assert cloud.stream_calls[0].messages[1].content == CURRENT
    assert isinstance(events[-1], StreamDone)
    assert consent.get_decision("req-stream").granted is True


@pytest.mark.asyncio
async def test_local_failure_after_start_commits_provider_no_fallback(tmp_path) -> None:
    settings = _settings(tmp_path)
    events = [
        StreamStart(request_id="req-stream", model="fake-model"),
        Token(request_id="req-stream", seq=0, text="partial"),
        StreamError(
            request_id="req-stream",
            error=ProviderUnavailable("dropped mid-stream"),
        ),
    ]
    local = _local(_scripted(events))
    cloud = _cloud(_raises(AssertionError("must never fall back after StreamStart")))
    router, _, _ = _harness(settings, [local, cloud], responder=_approve)

    collected = await _collect(router, _request())

    routed = collected[0]
    assert isinstance(routed, StreamRouted)
    assert routed.provider == "ollama"
    assert isinstance(collected[-1], StreamError)
    assert cloud.stream_calls == []
    assert len(local.stream_calls) == 1


@pytest.mark.asyncio
async def test_local_raise_after_start_commits_provider_no_fallback(tmp_path) -> None:
    settings = _settings(tmp_path)

    async def factory(_request: ChatRequest) -> AsyncIterator[object]:
        yield StreamStart(request_id="req-stream", model="fake-model")
        raise ProviderUnavailable("socket reset after start")

    local = _local(factory)
    cloud = _cloud(_raises(AssertionError("must never fall back after StreamStart")))
    router, _, _ = _harness(settings, [local, cloud], responder=_approve)

    collected: list[object] = []
    with pytest.raises(ProviderUnavailable):
        async for event in router.stream_chat(_request()):
            collected.append(event)
    # The router forwards routed + start, then raises: the caller (ChatService)
    # turns this into a failure. No retry and no second provider are used.
    assert [type(event) for event in collected] == [StreamRouted, StreamStart]
    assert len(local.stream_calls) == 1  # committed: no retry after StreamStart
    assert cloud.stream_calls == []


@pytest.mark.asyncio
async def test_explicit_local_failure_never_falls_back(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local(_raises(ProviderUnavailable("down")))
    cloud = _cloud(_scripted(_events("req-stream", "cloud")))
    router, _, _ = _harness(settings, [local, cloud], responder=_approve)

    with pytest.raises(ProviderUnavailable):
        await _collect(router, _request(), provider="ollama")

    assert cloud.stream_calls == []


@pytest.mark.asyncio
async def test_explicit_groq_failure_never_switches_to_local(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local(_scripted(_events("req-stream", "local")))
    cloud = _cloud(_raises(ProviderUnavailable("groq down")))
    router, _, _ = _harness(settings, [local, cloud], responder=_approve)

    with pytest.raises(ProviderUnavailable):
        await _collect(router, _request(), provider="groq")

    assert len(cloud.stream_calls) == 2  # one bounded retry, still no fallback
    assert local.stream_calls == []


# -- Cloud consent ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_explicit_groq_stream_requires_consent(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local(_scripted(_events("req-stream", "local")))
    cloud = _cloud(_scripted(_events("req-stream", "cloud")))
    router, bus, _ = _harness(settings, [local, cloud])  # no responder
    proposals: list[CloudFallbackProposed] = []
    bus.subscribe(CloudFallbackProposed, proposals.append)

    with pytest.raises(CloudConsentDeniedError):
        await _collect(router, _request(), provider="groq")

    assert cloud.stream_calls == []
    assert local.stream_calls == []
    assert len(proposals) == 1
    assert proposals[0].payload_fingerprint


@pytest.mark.asyncio
async def test_stream_consent_denial_makes_zero_cloud_requests(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local(_scripted(_events("req-stream", "local")))
    cloud = _cloud(_scripted(_events("req-stream", "cloud")))
    router, _, _ = _harness(settings, [local, cloud], responder=_deny)

    with pytest.raises(CloudConsentDeniedError):
        await _collect(router, _request(), provider="groq")

    assert cloud.stream_calls == []


# -- Actual serialized Groq HTTP body (streaming) ---------------------------------


def _groq_stream_transport(settings, captured: list[dict]) -> GroqProvider:
    sse = (
        'data: {"model": "%s", "choices": [{"delta": {"content": "hel"}}]}\n\n'
        'data: {"model": "%s", "choices": [{"delta": {"content": "lo"}}]}\n\n'
        'data: {"choices": [], "usage": {"total_tokens": 5}}\n\n'
        "data: [DONE]\n\n"
    ) % (settings.groq_model, settings.groq_model)

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            content=sse.encode("utf-8"),
            headers={"content-type": "text/event-stream"},
        )

    provider = GroqProvider(settings, transport=httpx.MockTransport(handler))
    provider.is_configured = lambda: True
    provider._auth_headers = lambda: {"Authorization": "Bearer test-key"}
    return provider


@pytest.mark.asyncio
async def test_actual_streaming_cloud_body_is_isolated(tmp_path) -> None:
    settings = _settings(tmp_path)
    captured: list[dict] = []
    groq = _groq_stream_transport(settings, captured)
    local = _local(_scripted(_events("req-stream", "local")))
    router, _, _ = _harness(settings, [local, groq], responder=_approve)

    try:
        events = await _collect(router, _request(), provider="groq")
    finally:
        await groq.aclose()

    bodies = captured
    assert len(bodies) == 1
    body = bodies[0]
    assert body["stream"] is True
    assert body["messages"] == [
        {"role": "system", "content": JARVIS_SYSTEM_PROMPT},
        {"role": "user", "content": CURRENT},
    ]
    assert body["temperature"] == 0.3
    assert body["max_tokens"] == 64
    serialized = json.dumps(body)
    for forbidden in (HISTORY, MEMORY, TOOL_OUTPUT, FILE_PATH, "older question"):
        assert forbidden not in serialized

    routed = events[0]
    assert isinstance(routed, StreamRouted)
    assert routed.provider == "groq"
    assert routed.cloud_consent == "granted"
    assert [e.text for e in events if isinstance(e, Token)] == ["hel", "lo"]
    assert isinstance(events[-1], StreamDone)
    assert events[-1].tokens_used == 5


@pytest.mark.asyncio
async def test_stream_consent_denial_never_reaches_groq_transport(tmp_path) -> None:
    settings = _settings(tmp_path)
    captured: list[dict] = []
    groq = _groq_stream_transport(settings, captured)
    router, _, _ = _harness(settings, [groq])  # no responder

    try:
        with pytest.raises(CloudConsentDeniedError):
            await _collect(router, _request(), provider="groq")
    finally:
        await groq.aclose()

    assert captured == []
