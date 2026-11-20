"""Mocked Ollama provider tests (Stage 2.2).

All HTTP traffic goes through ``httpx.MockTransport`` — no network access.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx
import pytest

from jarvis.intelligence.providers.base import (
    ChatMessage,
    ChatRequest,
    StreamDone,
    StreamError,
    StreamStart,
    Token,
)
from jarvis.intelligence.providers.errors import (
    ProviderInvalidRequest,
    ProviderResponseError,
    ProviderTimeout,
    ProviderUnavailable,
)
from jarvis.intelligence.providers.ollama_provider import OllamaProvider


def _settings(host: str = "http://ollama.test"):
    return type(
        "S",
        (),
        {"ollama_host": host, "ollama_model": "qwen3:8b", "ollama_keep_alive": "5m"},
    )()


def _provider(handler) -> OllamaProvider:
    return OllamaProvider(_settings(), transport=httpx.MockTransport(handler))


def _request(request_id: str = "req-1") -> ChatRequest:
    return ChatRequest(
        model=None,
        messages=[ChatMessage("user", "say hi")],
        request_id=request_id,
    )


def _ndjson(*records: dict) -> bytes:
    return ("\n".join(json.dumps(r) for r in records) + "\n").encode("utf-8")


def _stream_body():
    return [
        {"model": "qwen3:8b", "message": {"role": "assistant", "content": "hello"}, "done": False},
        {"model": "qwen3:8b", "message": {"role": "assistant", "content": " world"}, "done": False},
        {"model": "qwen3:8b", "message": {"role": "assistant", "content": ""}, "done": True, "eval_count": 42},
    ]


# -- Health --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_success_includes_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(
            200,
            json={"models": [{"name": "qwen3:8b"}, {"name": "mistral:7b"}]},
        )

    health = await _provider(handler).health()
    assert health.available is True
    assert [m.id for m in health.models] == ["qwen3:8b", "mistral:7b"]


@pytest.mark.asyncio
async def test_health_connection_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    health = await _provider(handler).health()
    assert health.available is False
    assert "unreachable" in health.detail


@pytest.mark.asyncio
async def test_health_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    health = await _provider(handler).health()
    assert health.available is False
    assert "timed out" in health.detail


@pytest.mark.asyncio
async def test_health_malformed_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json{{{")

    health = await _provider(handler).health()
    assert health.available is False
    assert "malformed" in health.detail


@pytest.mark.asyncio
async def test_health_unexpected_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    health = await _provider(handler).health()
    assert health.available is False
    assert "500" in health.detail


@pytest.mark.asyncio
async def test_list_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})

    models = await _provider(handler).list_models()
    assert [m.id for m in models] == ["qwen3:8b"]


@pytest.mark.asyncio
async def test_list_models_unavailable_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(ProviderUnavailable):
        await _provider(handler).list_models()


# -- Non-streaming chat ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_success_maps_payload() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "qwen3:8b",
                "message": {"role": "assistant", "content": "hi there"},
                "done": True,
                "eval_count": 17,
            },
        )

    response = await _provider(handler).chat(_request())
    assert response.content == "hi there"
    assert response.provider == "ollama"
    assert response.tokens_used == 17
    body = seen["body"]
    assert body["stream"] is False
    assert body["model"] == "qwen3:8b"           # default model applied
    assert body["keep_alive"] == "5m"
    assert body["options"]["temperature"] == 0.7
    assert body["messages"][0]["role"] == "user"


@pytest.mark.asyncio
async def test_chat_max_tokens_maps_to_num_predict() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"message": {"content": "x"}, "eval_count": 1}
        )

    request = _request()
    request.max_tokens = 8
    await _provider(handler).chat(request)
    assert seen["body"]["options"]["num_predict"] == 8


@pytest.mark.asyncio
async def test_chat_empty_content_is_safe() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": "qwen3:8b", "done": True})

    response = await _provider(handler).chat(_request())
    assert response.content == ""


@pytest.mark.asyncio
async def test_chat_malformed_response_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"definitely not json")

    with pytest.raises(ProviderResponseError):
        await _provider(handler).chat(_request())


@pytest.mark.asyncio
async def test_chat_unknown_model_maps_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model 'nope' not found"})

    request = ChatRequest(
        model="nope", messages=[ChatMessage("user", "x")], request_id="r"
    )
    with pytest.raises(ProviderInvalidRequest):
        await _provider(handler).chat(request)


@pytest.mark.asyncio
async def test_chat_http_500_maps_response_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(ProviderResponseError):
        await _provider(handler).chat(_request())


@pytest.mark.asyncio
async def test_chat_timeout_maps_provider_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ProviderTimeout):
        await _provider(handler).chat(_request())


# -- Streaming -------------------------------------------------------------------------


def _stream_handler(records=None, content: bytes | None = None):
    if content is not None:
        return lambda request: httpx.Response(
            200, content=content, headers={"content-type": "application/x-ndjson"}
        )
    return lambda request: httpx.Response(
        200, content=_ndjson(*(records if records is not None else _stream_body()))
    )


async def _collect(provider: OllamaProvider, request: ChatRequest) -> list:
    return [event async for event in provider.stream(request)]


@pytest.mark.asyncio
async def test_stream_full_flow() -> None:
    events = await _collect(_provider(_stream_handler()), _request("req-s1"))
    assert isinstance(events[0], StreamStart)
    assert events[0].request_id == "req-s1"
    assert events[0].model == "qwen3:8b"
    tokens = [e for e in events if isinstance(e, Token)]
    assert [t.seq for t in tokens] == [0, 1]           # monotonic
    assert "".join(t.text for t in tokens) == "hello world"
    assert all(t.request_id == "req-s1" for t in tokens)
    assert isinstance(events[-1], StreamDone)
    assert events[-1].content == "hello world"
    assert events[-1].tokens_used == 42


@pytest.mark.asyncio
async def test_stream_partial_chunks_multiple_lines() -> None:
    """One network chunk containing several NDJSON lines is parsed fully."""
    payload = _ndjson(*_stream_body())
    # Split the payload into odd fragments across "chunks".
    fragments = [payload[:9], payload[9:23], payload[23:]]

    async def fragmented() -> AsyncIterator[bytes]:  # noqa: F821
        for fragment in fragments:
            yield fragment
            await asyncio.sleep(0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=fragmented())

    events = await _collect(_provider(handler), _request("req-frag"))
    tokens = [e for e in events if isinstance(e, Token)]
    assert "".join(t.text for t in tokens) == "hello world"
    assert isinstance(events[-1], StreamDone)


@pytest.mark.asyncio
async def test_stream_empty_lines_tolerated() -> None:
    content = b'\n\n{"model":"qwen3:8b","message":{"content":"a"},"done":false}\n\n\n{"model":"qwen3:8b","message":{"content":""},"done":true,"eval_count":1}\n'
    events = await _collect(
        _provider(_stream_handler(content=content)), _request("req-bl")
    )
    tokens = [e for e in events if isinstance(e, Token)]
    assert [t.text for t in tokens] == ["a"]
    assert isinstance(events[-1], StreamDone)
    assert events[-1].tokens_used == 1


@pytest.mark.asyncio
async def test_stream_malformed_line_yields_stream_error() -> None:
    content = b'{"message":{"content":"ok"},"done":false}\n{not json at all}\n'
    events = await _collect(
        _provider(_stream_handler(content=content)), _request("req-bad")
    )
    # Tokens before the malformed line are preserved…
    assert any(isinstance(e, Token) for e in events)
    # …and the stream terminates with exactly one StreamError event.
    assert isinstance(events[-1], StreamError)
    assert isinstance(events[-1].error, ProviderResponseError)
    assert sum(isinstance(e, (StreamDone, StreamError)) for e in events) == 1


@pytest.mark.asyncio
async def test_stream_empty_response_yields_stream_error() -> None:
    events = await _collect(
        _provider(_stream_handler(content=b"\n\n")), _request("req-empty")
    )
    assert isinstance(events[-1], StreamError)


@pytest.mark.asyncio
async def test_stream_http_error_yields_stream_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "inference failed"})

    events = await _collect(_provider(handler), _request("req-500"))
    assert isinstance(events[-1], StreamError)
    assert "500" in events[-1].error.args[0]


@pytest.mark.asyncio
async def test_stream_timeout_maps_provider_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("chunk timeout", request=request)

    # Send-phase failure: the stream was never accepted, so the mapped
    # error is raised directly (no events, no StreamError event).
    with pytest.raises(ProviderTimeout):
        await _collect(_provider(handler), _request("req-to"))


# -- Cancellation & cleanup -----------------------------------------------------------


class _TrackingStream(httpx.AsyncByteStream):
    """Slow NDJSON byte stream that reports when httpx closes it."""

    def __init__(self, tracker: dict[str, bool]) -> None:
        self._tracker = tracker

    async def __aiter__(self) -> AsyncIterator[bytes]:  # noqa: F821
        for _ in range(100):
            yield _ndjson({"message": {"content": "x"}, "done": False})
            await asyncio.sleep(0.01)

    async def aclose(self) -> None:
        self._tracker["closed"] = True


@pytest.mark.asyncio
async def test_stream_cancellation_closes_response() -> None:
    tracker: dict[str, bool] = {"closed": False}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_TrackingStream(tracker))

    provider = OllamaProvider(_settings(), transport=httpx.MockTransport(handler))
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
    await asyncio.sleep(0.05)  # let some tokens flow
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Cancellation must NEVER become StreamError.
    assert not any(isinstance(e, StreamError) for e in events)
    assert any(isinstance(e, Token) for e in events)
    assert tracker["closed"] is True  # HTTP response actually closed


# -- Thinking-model configuration -------------------------------------------------------


def _think_provider(handler) -> OllamaProvider:
    return OllamaProvider(
        type(
            "S",
            (),
            {
                "ollama_host": "http://ollama.test",
                "ollama_model": "qwen3:8b",
                "ollama_keep_alive": "5m",
                "ollama_think": False,
            },
        )(),
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_think_flag_sent_in_body() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"message": {"content": "ok"}, "eval_count": 1}
        )

    await _think_provider(handler).chat(_request())
    assert seen["body"]["think"] is False  # type: ignore[index]


@pytest.mark.asyncio
async def test_chat_graceful_retry_without_think() -> None:
    """Non-thinking models on newer Ollama reject the think field."""
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if "think" in body:
            return httpx.Response(
                400, json={"error": "model does not support thinking"}
            )
        return httpx.Response(
            200, json={"message": {"content": "answer"}, "eval_count": 2}
        )

    response = await _think_provider(handler).chat(_request())
    assert response.content == "answer"
    assert len(calls) == 2
    assert "think" in calls[0]
    assert "think" not in calls[1]


@pytest.mark.asyncio
async def test_stream_graceful_retry_without_think() -> None:
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if "think" in body:
            return httpx.Response(
                400, json={"error": "model does not support thinking"}
            )
        return httpx.Response(200, content=_ndjson(*_stream_body()))

    events = await _collect(_think_provider(handler), _request("req-think"))
    assert isinstance(events[-1], StreamDone)
    assert len(calls) == 2
    assert "think" not in calls[1]


# -- Contract compatibility ------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_events_are_serializable_dataclasses() -> None:
    events = await _collect(_provider(_stream_handler()), _request("req-ser"))
    import dataclasses

    for event in events:
        assert dataclasses.is_dataclass(event)
