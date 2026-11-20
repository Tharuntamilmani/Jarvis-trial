"""Shared fakes for ChatService tests (Stage 2.5)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path

from jarvis.config import Settings
from jarvis.core.event_bus import EventBus
from jarvis.intelligence.chat_service import ChatService
from jarvis.intelligence.providers.base import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    LLMProvider,
    ModelInfo,
    PrivacyLevel,
    ProviderCapabilities,
    ProviderHealth,
    StreamDone,
    StreamStart,
    Token,
)
from jarvis.intelligence.providers.errors import ProviderInvalidRequest
from jarvis.intelligence.router import StreamRouted

USER_MESSAGE = ChatMessage("user", "hello")


def chat_settings(tmp_path: Path, **overrides) -> Settings:
    defaults = dict(
        log_dir=tmp_path / "logs",
        chat_max_concurrent_generations=1,
        chat_concurrency_policy="reject",
        chat_completed_cache_size=8,
        _env_file=None,
    )
    defaults.update(overrides)
    return Settings(**defaults)


class FakeRouter:
    """Router test double matching the real ``stream_chat`` contract."""

    def __init__(
        self,
        script: Callable[[ChatRequest], AsyncIterator[object]] | None = None,
        *,
        provider: str = "fake",
        model: str = "fake-model",
        privacy: str = "local",
        cloud_consent: str = "",
        raise_before: Exception | None = None,
        scripts: list[Callable[[ChatRequest], AsyncIterator[object]]] | None = None,
    ) -> None:
        self._script = script
        self._scripts = list(scripts or [])
        self._provider = provider
        self._model = model
        self._privacy = privacy
        self._cloud_consent = cloud_consent
        self._raise_before = raise_before
        self.calls: list[ChatRequest] = []

    def validate_request(self, request: ChatRequest) -> None:
        if not request.messages:
            raise ProviderInvalidRequest("chat request has no messages")

    async def stream_chat(
        self,
        request: ChatRequest,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> AsyncIterator[object]:
        self.calls.append(request)
        if self._raise_before is not None:
            raise self._raise_before

        selected_provider = provider or self._provider
        selected_model = model or self._model
        yield StreamRouted(
            request_id=request.request_id,
            provider=selected_provider,
            model=selected_model,
            privacy=self._privacy,
            cloud_consent=self._cloud_consent,
        )

        script = self._scripts.pop(0) if self._scripts else self._script
        if script is None:
            return
        generator = script(request)
        try:
            async for event in generator:
                yield event
        finally:
            await generator.aclose()


def simple_script(
    chunks: tuple[str, ...] = ("hello",),
    *,
    tokens_used: int = 3,
    model: str = "fake-model",
    delay: float = 0.0,
    terminal: bool = True,
) -> Callable[[ChatRequest], AsyncIterator[object]]:
    """A well-formed stream: StreamStart, contiguous tokens, StreamDone."""

    async def script(request: ChatRequest) -> AsyncIterator[object]:
        yield StreamStart(request_id=request.request_id, model=model)
        for seq, text in enumerate(chunks):
            yield Token(request_id=request.request_id, seq=seq, text=text)
            if delay:
                await asyncio.sleep(delay)
        if terminal:
            yield StreamDone(
                request_id=request.request_id,
                content="".join(chunks),
                tokens_used=tokens_used,
            )

    return script


def scripted(
    parts: list[object],
) -> Callable[[ChatRequest], AsyncIterator[object]]:
    """Yield raw events; callables are evaluated with the request."""

    async def script(request: ChatRequest) -> AsyncIterator[object]:
        for part in parts:
            event = part(request) if callable(part) else part
            if event is not None:
                yield event

    return script


def make_service(
    settings: Settings,
    router,
    *,
    bus: EventBus | None = None,
    lifecycle=None,
    kill_switch=None,
    id_factory=None,
) -> tuple[ChatService, EventBus]:
    bus = bus or EventBus()
    service = ChatService(
        settings,
        bus,
        router,
        lifecycle=lifecycle,
        kill_switch=kill_switch,
        id_factory=id_factory,
    )
    return service, bus


class FakeLifecycle:
    """Minimal killable-task registry used to exercise the kill switch."""

    def __init__(self) -> None:
        self.tasks: set[asyncio.Task] = set()
        self.kill_switch_active = False

    def register_killable_task(self, task: asyncio.Task) -> None:
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def trip(self) -> None:
        self.kill_switch_active = True
        for task in list(self.tasks):
            if not task.done():
                task.cancel()


class StreamingProvider(LLMProvider):
    """Real-provider-shaped fake for integration through the real router."""

    def __init__(
        self,
        provider_id: str,
        privacy: PrivacyLevel,
        factory: Callable[[ChatRequest], AsyncIterator[object]],
    ) -> None:
        self.id = provider_id
        self.capabilities = ProviderCapabilities(
            streaming=True,
            tools=True,
            context_size=100_000,
            privacy=privacy,
        )
        self._factory = factory
        self.stream_calls: list[ChatRequest] = []
        self.closed = 0

    async def health(self) -> ProviderHealth:
        return ProviderHealth(available=True, detail="connected")

    async def list_models(self) -> list[ModelInfo]:
        return []

    async def chat(self, request: ChatRequest) -> ChatResponse:
        raise AssertionError("streaming tests never call chat()")

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
        return True
