"""Live Ollama integration tests (Stage 2.2).

Real requests against the locally installed Ollama service and the
already-pulled ``qwen3:8b`` model. Entirely local and free. Skipped
cleanly when Ollama is unreachable.
"""

from __future__ import annotations

import asyncio
import os

import httpx
import pytest

OLLAMA_HOST = os.environ.get("JARVIS_TEST_OLLAMA_HOST", "http://127.0.0.1:11434")
EXPECTED_MODEL = "qwen3:8b"


def _ollama_available() -> bool:
    async def probe() -> bool:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get(f"{OLLAMA_HOST}/api/tags")
                return response.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    try:
        return asyncio.run(asyncio.wait_for(probe(), timeout=5))
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not _ollama_available(), reason="no local Ollama service available"
)


def _provider():
    from jarvis.intelligence.providers.ollama_provider import OllamaProvider

    settings = type(
        "S",
        (),
        {
            "ollama_host": OLLAMA_HOST,
            "ollama_model": EXPECTED_MODEL,
            "ollama_keep_alive": "10m",
        },
    )()
    return OllamaProvider(settings)


@pytest.mark.asyncio
async def test_live_health_detects_qwen3() -> None:
    health = await _provider().health()
    assert health.available is True, health.detail
    assert EXPECTED_MODEL in [m.id for m in health.models]


@pytest.mark.asyncio
async def test_live_list_models() -> None:
    models = await _provider().list_models()
    assert EXPECTED_MODEL in [m.id for m in models]


@pytest.mark.asyncio
async def test_live_short_chat_response() -> None:
    from jarvis.intelligence.providers.base import ChatMessage, ChatRequest

    provider = _provider()
    request = ChatRequest(
        model=EXPECTED_MODEL,
        messages=[ChatMessage("user", "Reply with exactly: ONLINE")],
        request_id="live-chat-1",
        max_tokens=64,  # provider sends think:false (default setting), so
        # the answer streams directly on the content channel.
    )
    response = await provider.chat(request)
    assert response.provider == "ollama"
    assert response.model == EXPECTED_MODEL
    assert response.content.strip() != ""


@pytest.mark.asyncio
async def test_live_streamed_response() -> None:
    from jarvis.intelligence.providers.base import (
        ChatMessage,
        ChatRequest,
        StreamDone,
        StreamStart,
        Token,
    )

    provider = _provider()
    request = ChatRequest(
        model=EXPECTED_MODEL,
        # Provider default sends think:false, keeping the answer channel
        # deterministic for this thinking model.
        messages=[ChatMessage("user", "Count from 1 to 5, digits only.")],
        request_id="live-stream-1",
        max_tokens=64,
    )
    events = []
    async for event in provider.stream(request):
        events.append(event)

    assert isinstance(events[0], StreamStart)
    tokens = [e for e in events if isinstance(e, Token)]
    assert len(tokens) >= 1
    assert isinstance(events[-1], StreamDone)
    assert events[-1].request_id == "live-stream-1"
    assert "".join(t.text for t in tokens) == events[-1].content
