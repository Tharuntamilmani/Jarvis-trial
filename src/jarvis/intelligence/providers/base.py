"""LLM provider abstraction.

One interface for all providers (Ollama, Groq, future clouds): streaming,
chat completion, availability and errors share a single contract.

Streaming contract (fixed in Stage 2.1):

- ``stream()`` yields typed :data:`StreamEvent` objects.
- The first event is always :class:`StreamStart`.
- Token events carry a monotonic per-request ``seq`` (deduplication and
  ordering enforcement belong to the ChatService layer).
- Exactly ONE terminal event ends a stream: :class:`StreamDone`,
  :class:`StreamError`, or :class:`StreamCancelled`.
- ``asyncio.CancelledError`` is never converted into a stream event: the
  provider closes its HTTP stream and re-raises it unchanged.

Cancellation contract:

- Callers cancel via standard ``asyncio.Task.cancel()`` on the consuming
  task. Providers must close their active HTTP response on cancellation
  and propagate ``CancelledError`` (see the provider contract tests).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import AsyncIterator, Union

from jarvis.intelligence.providers.errors import ProviderError


class PrivacyLevel(StrEnum):
    """Where conversation data goes when using this provider."""

    LOCAL = "local"      # data never leaves the machine
    CLOUD = "cloud"      # data leaves the machine (consent required)


@dataclass(frozen=True)
class ProviderCapabilities:
    streaming: bool
    tools: bool
    context_size: int
    privacy: PrivacyLevel


@dataclass(frozen=True)
class ModelInfo:
    id: str
    name: str
    context_size: int = 0


@dataclass
class ChatMessage:
    role: str  # system | user | assistant | tool
    content: str


@dataclass
class ChatRequest:
    """A chat completion request.

    ``model`` may be ``None`` meaning "provider default". ``request_id``
    correlates stream events and cancellation; ChatService guarantees it is
    non-empty before a provider ever sees the request.
    """

    model: str | None
    messages: list[ChatMessage]
    request_id: str = ""
    temperature: float = 0.7
    max_tokens: int | None = None
    timeout: float | None = None  # seconds; None = provider/router default


@dataclass
class ChatResponse:
    content: str
    model: str
    provider: str
    tokens_used: int = 0
    finish_reason: str = "stop"  # stop | length | error


@dataclass
class ProviderHealth:
    available: bool
    detail: str = ""
    models: tuple[ModelInfo, ...] = field(default_factory=tuple)
    # A hard connection failure (e.g. local service refused the socket).
    # The router may use a *fresh* hard-down snapshot to skip one local
    # attempt; stale health never suppresses a real attempt.
    hard_down: bool = False


# --- Typed stream events ------------------------------------------------------


@dataclass(frozen=True)
class StreamStart:
    """First event of every stream; confirms the request was accepted."""

    request_id: str
    model: str


@dataclass(frozen=True)
class Token:
    """One streamed content chunk. ``seq`` is monotonic per request."""

    request_id: str
    seq: int
    text: str


@dataclass(frozen=True)
class StreamDone:
    """Terminal success event."""

    request_id: str
    content: str
    tokens_used: int = 0


@dataclass(frozen=True)
class StreamError:
    """Terminal failure event (partial content is kept by the caller).

    Never emitted for cancellation — cancellation is
    :class:`StreamCancelled` / ``asyncio.CancelledError``.
    """

    request_id: str
    error: ProviderError


@dataclass(frozen=True)
class StreamCancelled:
    """Terminal cancellation event (Stop button / kill switch).

    Emitted by the ChatService layer when the consuming task is cancelled;
    providers themselves only see ``asyncio.CancelledError``.
    """

    request_id: str
    partial_content: str


StreamEvent = Union[StreamStart, Token, StreamDone, StreamError, StreamCancelled]


class LLMProvider(ABC):
    """Common interface for every AI provider."""

    id: str = "base"
    capabilities: ProviderCapabilities

    @abstractmethod
    async def health(self) -> ProviderHealth:
        """Report availability (never raises)."""

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        """List models usable through this provider."""

    @abstractmethod
    async def chat(self, request: ChatRequest) -> ChatResponse:
        """Non-streaming chat completion."""

    @abstractmethod
    def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        """Streaming chat completion yielding typed stream events.

        Contract: ``StreamStart`` first, exactly one terminal event last,
        ``CancelledError`` propagates unchanged.
        """

    def _raise_unimplemented(self, method: str) -> None:
        raise ProviderError(
            f"{self.id} provider: {method} is not implemented yet "
            "(arrives in a later stage of Phase 2)"
        )

    def is_configured(self) -> bool:
        """Whether the provider could accept requests at all (no network).

        The router needs a no-HTTP "configured?" probe: a cloud provider
        without stored credentials can never be selected, while a local
        provider is always configured. ``ProviderHealth.available``
        conflates configuration with reachability, hence this method
        (minimal additive interface, Stage 2.4).
        """
        return True
