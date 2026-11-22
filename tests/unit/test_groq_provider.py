"""Mocked Groq provider tests (Stage 2.3).

All HTTP traffic goes through ``httpx.MockTransport`` — no network access,
no paid API usage. The API key is stored through the real DPAPI
``CredentialStore`` into a per-test temp directory.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from collections.abc import AsyncIterator

import httpx
import pytest

from jarvis.config import Settings
from jarvis.intelligence.providers.base import (
    ChatMessage,
    ChatRequest,
    StreamDone,
    StreamError,
    StreamStart,
    Token,
)
from jarvis.intelligence.providers.errors import (
    ProviderAuthError,
    ProviderInvalidRequest,
    ProviderRateLimited,
    ProviderResponseError,
    ProviderTimeout,
    ProviderUnavailable,
    normalize_retry_after,
)
from jarvis.intelligence.providers.groq_provider import (
    GroqProvider,
    parse_retry_after,
)
from jarvis.security.credentials import CredentialStore

TEST_KEY = "gsk_test_key_0123456789abcdef"


@pytest.fixture()
def groq_settings(tmp_path) -> Settings:
    return Settings(
        log_dir=tmp_path / "logs",
        groq_model="llama-3.3-70b-versatile",
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture()
def store(groq_settings) -> CredentialStore:
    return CredentialStore(groq_settings)


def _provider(groq_settings, handler) -> GroqProvider:
    return GroqProvider(groq_settings, transport=httpx.MockTransport(handler))


def _request(request_id: str = "req-g1") -> ChatRequest:
    return ChatRequest(
        model=None,  # provider default model
        messages=[ChatMessage("user", "say hi")],
        request_id=request_id,
    )


def _ok_chat_body() -> dict:
    return {
        "id": "chatcmpl-x",
        "model": "llama-3.3-70b-versatile",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "hi from groq"},
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 4, "total_tokens": 9},
    }


def _sse(*events: str) -> bytes:
    return ("".join(events)).encode("utf-8")


def _sse_data(payload: str) -> str:
    return f"data: {payload}\n\n"


def _delta_events(tokens: list[str]) -> list[str]:
    out = []
    for t in tokens:
        out.append(
            _sse_data(
                json.dumps(
                    {
                        "choices": [
                            {"delta": {"content": t}, "index": 0},
                        ]
                    }
                )
            )
        )
    return out


# -- Credentials ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_credentials_zero_http(groq_settings) -> None:
    request_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        request_count["n"] += 1
        return httpx.Response(200, json={"data": []})

    provider = _provider(groq_settings, handler)
    health = await provider.health()
    assert health.available is False
    assert "not configured" in health.detail
    with pytest.raises(ProviderUnavailable):
        await provider.chat(_request())
    with pytest.raises(ProviderUnavailable):
        await provider.list_models()
    with pytest.raises(ProviderUnavailable):
        async for _ in provider.stream(_request()):
            pass
    assert request_count["n"] == 0  # ZERO HTTP requests without a key


@pytest.mark.asyncio
async def test_credential_redaction_everywhere(groq_settings, store, caplog) -> None:
    """The key must never appear in health details, exceptions, or logs."""
    store.store_secret("groq_api_key", TEST_KEY)

    def handler(request: httpx.Request) -> httpx.Response:
        # Authorization header is present on the wire...
        assert request.headers["Authorization"] == f"Bearer {TEST_KEY}"
        return httpx.Response(401, json={"error": "invalid"})

    provider = _provider(groq_settings, handler)
    with caplog.at_level(logging.DEBUG):
        health = await provider.health()
        assert "authentication failed" in health.detail
        assert TEST_KEY not in health.detail
        with pytest.raises(ProviderAuthError) as exc_info:
            await provider.chat(_request())
        assert TEST_KEY not in str(exc_info.value)
        assert TEST_KEY not in caplog.text
    store.delete_secret("groq_api_key")


@pytest.mark.asyncio
async def test_valid_credential_health_success(groq_settings, store) -> None:
    store.store_secret("groq_api_key", TEST_KEY)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(
            200,
            json={"data": [{"id": "llama-3.3-70b-versatile"}, {"id": "whisper-large-v3"}]},
        )

    health = await _provider(groq_settings, handler).health()
    assert health.available is True
    ids = [m.id for m in health.models]
    assert "llama-3.3-70b-versatile" in ids
    assert "whisper-large-v3" in ids  # listed, but not assumed chat-capable
    store.delete_secret("groq_api_key")


# -- Error mapping ------------------------------------------------------------------------


def _authed(groq_settings, store, handler) -> GroqProvider:
    store.store_secret("groq_api_key", TEST_KEY)
    return _provider(groq_settings, handler)


@pytest.mark.asyncio
async def test_http_401_maps_auth_error(groq_settings, store) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid api key"})

    with pytest.raises(ProviderAuthError):
        await _authed(groq_settings, store, handler).chat(_request())
    store.delete_secret("groq_api_key")


@pytest.mark.asyncio
async def test_http_403_maps_auth_error(groq_settings, store) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    with pytest.raises(ProviderAuthError):
        await _authed(groq_settings, store, handler).chat(_request())
    store.delete_secret("groq_api_key")


@pytest.mark.asyncio
async def test_http_400_maps_invalid_request(groq_settings, store) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "model not chat capable"})

    with pytest.raises(ProviderInvalidRequest):
        await _authed(groq_settings, store, handler).chat(_request())
    store.delete_secret("groq_api_key")


@pytest.mark.asyncio
async def test_http_429_integer_retry_after(groq_settings, store) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, json={"error": "rate limited"}, headers={"Retry-After": "7"}
        )

    with pytest.raises(ProviderRateLimited) as exc_info:
        await _authed(groq_settings, store, handler).chat(_request())
    assert exc_info.value.retry_after == 7.0
    store.delete_secret("groq_api_key")


@pytest.mark.asyncio
async def test_http_429_httpdate_retry_after(groq_settings, store) -> None:
    from datetime import datetime, timedelta, timezone

    future = datetime.now(timezone.utc) + timedelta(seconds=12)
    http_date = future.strftime("%a, %d %b %Y %H:%M:%S GMT")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, json={"error": "rate limited"}, headers={"Retry-After": http_date}
        )

    with pytest.raises(ProviderRateLimited) as exc_info:
        await _authed(groq_settings, store, handler).chat(_request())
    assert 10.0 <= exc_info.value.retry_after <= 12.0  # parsed HTTP-date
    store.delete_secret("groq_api_key")


def test_retry_after_bounds() -> None:
    assert parse_retry_after("120") == 120.0
    assert normalize_retry_after(parse_retry_after("120")) == 30.0  # clamped
    assert normalize_retry_after(parse_retry_after("0")) == 1.0
    assert parse_retry_after("garbage") is None
    assert parse_retry_after(None) is None


@pytest.mark.asyncio
async def test_connection_refused_and_timeout(groq_settings, store) -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(ProviderUnavailable):
        await _authed(groq_settings, store, refused).chat(_request())

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ProviderTimeout):
        await _authed(groq_settings, store, slow).chat(_request())
    store.delete_secret("groq_api_key")


# -- Non-streaming chat ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_success_and_payload(groq_settings, store) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization", "").startswith("Bearer ")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_ok_chat_body())

    response = await _authed(groq_settings, store, handler).chat(_request())
    assert response.content == "hi from groq"
    assert response.provider == "groq"
    assert response.model == "llama-3.3-70b-versatile"  # default applied
    assert response.tokens_used == 9
    assert response.finish_reason == "stop"
    assert seen["auth"] is True
    body = seen["body"]
    assert body["stream"] is False
    assert body["model"] == "llama-3.3-70b-versatile"
    assert body["messages"] == [{"role": "user", "content": "say hi"}]
    store.delete_secret("groq_api_key")


@pytest.mark.asyncio
async def test_chat_empty_choices_is_safe(groq_settings, store) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": "m", "choices": [], "usage": {}})

    response = await _authed(groq_settings, store, handler).chat(_request())
    assert response.content == ""
    assert response.finish_reason == "error"
    store.delete_secret("groq_api_key")


@pytest.mark.asyncio
async def test_chat_malformed_body_raises(groq_settings, store) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json{[")

    with pytest.raises(ProviderResponseError):
        await _authed(groq_settings, store, handler).chat(_request())
    store.delete_secret("groq_api_key")


# -- SSE streaming --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_full_flow_with_done(groq_settings, store) -> None:
    events_data = _delta_events(["hello", " ", "cloud"]) + [
        _sse_data(json.dumps({"choices": [], "usage": {"total_tokens": 12}})),
        "data: [DONE]\n\n",
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Accept"] == "text/event-stream"
        return httpx.Response(200, content=_sse(*events_data))

    events = [
        e
        async for e in _authed(groq_settings, store, handler).stream(_request("req-s1"))
    ]
    assert isinstance(events[0], StreamStart)
    assert events[0].request_id == "req-s1"
    assert events[0].model == "llama-3.3-70b-versatile"
    tokens = [e for e in events if isinstance(e, Token)]
    assert [t.seq for t in tokens] == [0, 1, 2]  # monotonic
    assert "".join(t.text for t in tokens) == "hello cloud"
    assert all(t.request_id == "req-s1" for t in tokens)
    assert isinstance(events[-1], StreamDone)
    assert events[-1].content == "hello cloud"
    assert events[-1].tokens_used == 12  # usage parsed
    assert sum(isinstance(e, (StreamDone, StreamError)) for e in events) == 1
    store.delete_secret("groq_api_key")


@pytest.mark.asyncio
async def test_stream_partial_sse_chunks(groq_settings, store) -> None:
    payload = _sse(*(_delta_events(["a", "b"]) + ["data: [DONE]\n\n"]))
    fragments = [payload[:11], payload[11:19], payload[19:]]

    async def fragmented() -> AsyncIterator[bytes]:
        for f in fragments:
            yield f
            await asyncio.sleep(0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=fragmented())

    events = [
        e async for e in _authed(groq_settings, store, handler).stream(_request("req-frag"))
    ]
    tokens = [e for e in events if isinstance(e, Token)]
    assert "".join(t.text for t in tokens) == "ab"
    assert isinstance(events[-1], StreamDone)
    store.delete_secret("groq_api_key")


@pytest.mark.asyncio
async def test_stream_sse_comments_and_multi_data(groq_settings, store) -> None:
    """Comments and keep-alives are skipped; multi-line data events parse."""
    content = (
        ": keep-alive comment\n"
        "\n"
        'data: {"choices":[{"delta":{"content":"x"}}]}\n'
        ": ping\n"
        'data: {"choices":[{"delta":{"content":"y"}}]}\n'
        "\n"
        "data: [DONE]\n\n"
    ).encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content)

    events = [
        e async for e in _authed(groq_settings, store, handler).stream(_request())
    ]
    tokens = [e for e in events if isinstance(e, Token)]
    assert "".join(t.text for t in tokens) == "xy"
    assert isinstance(events[-1], StreamDone)
    store.delete_secret("groq_api_key")


@pytest.mark.asyncio
async def test_stream_malformed_record_yields_stream_error(
    groq_settings, store
) -> None:
    content = b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: {bad json}\n\n'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content)

    events = [
        e
        async for e in _authed(groq_settings, store, handler).stream(
            _request("req-bad")
        )
    ]
    assert any(isinstance(e, Token) for e in events)  # partial content kept
    assert isinstance(events[-1], StreamError)
    assert isinstance(events[-1].error, ProviderResponseError)
    store.delete_secret("groq_api_key")


@pytest.mark.asyncio
async def test_stream_mid_stream_disconnection(groq_settings, store) -> None:
    async def broken_body() -> AsyncIterator[bytes]:
        yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        raise httpx.ReadError("connection lost")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=broken_body())

    events = [
        e async for e in _authed(groq_settings, store, handler).stream(_request("req-disc"))
    ]
    assert any(isinstance(e, Token) for e in events)
    assert isinstance(events[-1], StreamError)
    store.delete_secret("groq_api_key")


@pytest.mark.asyncio
async def test_stream_closes_response_on_cancellation(
    groq_settings, store
) -> None:
    tracker: dict[str, bool] = {"closed": False}

    class _TrackingStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            for _ in range(100):
                yield b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'
                await asyncio.sleep(0.01)

        async def aclose(self) -> None:
            tracker["closed"] = True

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_TrackingStream())

    provider = _authed(groq_settings, store, handler)
    events: list[object] = []
    gen = provider.stream(_request("req-cancel"))

    async def consume() -> None:
        try:
            async for event in gen:
                events.append(event)
                await asyncio.sleep(0)
        finally:
            await gen.aclose()  # what ChatService does on cancellation

    task = asyncio.ensure_future(consume())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not any(isinstance(e, StreamError) for e in events)  # never on cancel
    assert any(isinstance(e, Token) for e in events)
    assert tracker["closed"] is True  # HTTP response actually closed
    store.delete_secret("groq_api_key")


# -- Contract compatibility ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_events_are_serializable_dataclasses(
    groq_settings, store
) -> None:
    events_data = _delta_events(["ok"]) + ["data: [DONE]\n\n"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse(*events_data))

    events = [
        e async for e in _authed(groq_settings, store, handler).stream(_request())
    ]
    for event in events:
        assert dataclasses.is_dataclass(event)
    store.delete_secret("groq_api_key")


@pytest.mark.asyncio
async def test_early_close_closes_line_iterator_before_response(
    groq_settings, store
) -> None:
    """Early exit must close aiter_lines() before the HTTP response."""

    class _FakeStream:
        def __init__(self, order: list[str]) -> None:
            self.order = order

        async def aclose(self) -> None:
            self.order.append("stream")

    class _FakeResponse:
        status_code = 200

        def __init__(self) -> None:
            self.order: list[str] = []
            self.lines_gen = None
            self.stream = _FakeStream(self.order)

        def aiter_lines(self):
            self.lines_gen = self._lines()
            return self.lines_gen

        async def _lines(self):
            try:
                yield (
                    'data: {"choices":[{"delta":{"content":"first"}}]}\n'
                )
                yield (
                    'data: {"choices":[{"delta":{"content":"second"}}]}\n'
                )
            finally:
                self.order.append("lines")

        async def aclose(self) -> None:
            self.order.append("response")

    class _FakeClient:
        def __init__(self, response: _FakeResponse) -> None:
            self.response = response

        def build_request(self, *args, **kwargs):
            return object()

        async def send(self, request, stream: bool = False):
            return self.response

        async def aclose(self) -> None:
            return None

    response = _FakeResponse()
    provider = _authed(groq_settings, store, lambda request: None)
    provider._auth_headers = lambda: {"Authorization": "Bearer test"}  # noqa: SLF001
    await provider._client.aclose()  # noqa: SLF001 - test swap
    provider._client = _FakeClient(response)  # noqa: SLF001

    gen = provider.stream(_request("req-early-close"))
    assert isinstance(await gen.__anext__(), StreamStart)
    assert isinstance(await gen.__anext__(), Token)
    await gen.aclose()

    assert response.order == ["stream", "lines", "response"]
    await provider.aclose()
    store.delete_secret("groq_api_key")
