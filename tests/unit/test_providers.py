"""Provider tests: Groq stub (Phase 1) + real Ollama provider (Stage 2.2)."""

from __future__ import annotations

import pytest

from jarvis.core.event_bus import EventBus
from jarvis.core.events import ProviderHealthChanged
from jarvis.intelligence.providers.base import ChatRequest, ChatMessage
from jarvis.intelligence.providers.errors import ProviderError
from jarvis.intelligence.providers.groq_provider import GroqProvider
from jarvis.intelligence.providers.ollama_provider import OllamaProvider
from jarvis.intelligence.providers.registry import ProviderRegistry


def _ollama_settings(host: str = "", model: str = "qwen3:8b"):
    return type(
        "S",
        (),
        {"ollama_host": host, "ollama_model": model, "ollama_keep_alive": "5m"},
    )()


def _groq_settings(tmp_path):
    from jarvis.config import Settings

    return Settings(
        log_dir=tmp_path / "logs",
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.mark.asyncio
async def test_provider_health_states(tmp_path) -> None:
    registry = ProviderRegistry(EventBus())
    registry.register(OllamaProvider(_ollama_settings(host="")))  # invalid host
    registry.register(GroqProvider(_groq_settings(tmp_path)))  # no stored key
    health = await registry.refresh_health()
    assert registry.ids == ("ollama", "groq")
    # Real Ollama provider: a misconfigured endpoint reports unavailable with a
    # provider-specific detail (no longer the Phase 1 stub text).
    assert health["ollama"].available is False
    assert "misconfigured" in health["ollama"].detail
    # Real Groq provider without a stored key: not configured, no HTTP made.
    assert health["groq"].available is False
    assert "not configured" in health["groq"].detail
    snapshot = registry.health_snapshot()
    assert snapshot["ollama"]["privacy"] == "local"
    assert snapshot["groq"]["privacy"] == "cloud"


@pytest.mark.asyncio
async def test_invalid_endpoint_chat_raises_provider_error(tmp_path) -> None:
    provider = OllamaProvider(_ollama_settings(model="m"))
    request = ChatRequest(model="m", messages=[ChatMessage("user", "hi")], request_id="r1")
    with pytest.raises(ProviderError):
        await provider.chat(request)
    with pytest.raises(ProviderError):
        async for _ in provider.stream(request):
            pass


@pytest.mark.asyncio
async def test_health_change_events_published() -> None:
    bus = EventBus()
    events: list[ProviderHealthChanged] = []
    bus.subscribe(ProviderHealthChanged, events.append)
    registry = ProviderRegistry(bus)
    registry.register(OllamaProvider(_ollama_settings(host="", model="m")))
    await registry.refresh_health()
    await registry.refresh_health()  # second pass: no change → no event
    assert len(events) == 1
    assert events[0].provider == "ollama"
    assert events[0].available is False


@pytest.mark.asyncio
async def test_privacy_levels(tmp_path) -> None:
    ollama = OllamaProvider(_ollama_settings(model="m"))
    groq = GroqProvider(_groq_settings(tmp_path))
    assert ollama.capabilities.privacy.value == "local"
    assert groq.capabilities.privacy.value == "cloud"
