"""Chat orchestration and streaming intelligence engine (Stage 2.5).

``ChatService`` is the core (UI-agnostic) layer between the provider router
and any future native Workspace chat interface:

- assigns and tracks unique request IDs,
- consumes :meth:`LLMRouter.stream_chat` on an owning asyncio task,
- enforces strict contiguous token sequencing (public sequence is owned
  here; provider sequence numbers are validated separately),
- publishes the typed chat events (started / token / terminal),
- preserves partial content on failure and cancellation,
- implements precise, request-scoped cancellation,
- bounds concurrent generations (reject or queue policy),
- aggregates the assistant state (speaking > thinking > idle),
- honours the kill switch and closes every active task on shutdown.

Exactly one terminal event is published per request. No PySide6 import is
allowed here: the desktop layer consumes this service through the event
bus and the core facade.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from jarvis.config import Settings
from jarvis.core.event_bus import EventBus
from jarvis.core.events import (
    AssistantStateChanged,
    ChatStreamCancelled,
    ChatStreamCompleted,
    ChatStreamFailed,
    ChatStreamStarted,
    ChatToken,
)
from jarvis.core.exceptions import JarvisError, KillSwitchActiveError
from jarvis.intelligence.providers.base import (
    ChatMessage,
    ChatRequest,
    StreamCancelled,
    StreamDone,
    StreamError,
    StreamStart,
    Token,
)
from jarvis.intelligence.providers.errors import ProviderError, ProviderResponseError
from jarvis.intelligence.router import LLMRouter, StreamRouted
from jarvis.monitoring.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from jarvis.core.lifecycle import LifecycleManager
    from jarvis.security.kill_switch import KillSwitchService

log = get_logger(__name__)

_MAX_ID_ATTEMPTS = 5
_MAX_FAILURE_DETAIL = 300


# -- Errors -------------------------------------------------------------------


class ChatServiceError(JarvisError):
    """Base error for chat orchestration failures."""


class DuplicateRequestIdError(ChatServiceError):
    """An explicitly supplied request ID is already active."""


class ChatCapacityError(ChatServiceError):
    """The configured concurrent-generation limit has been reached."""


class ChatRequestNotFoundError(ChatServiceError):
    """The request ID is neither active nor retained in the result cache."""


# -- Public value types -------------------------------------------------------


class AssistantState(StrEnum):
    """Aggregate assistant state shared by every desktop mode."""

    IDLE = "idle"
    THINKING = "thinking"
    SPEAKING = "speaking"


class ChatStatus(StrEnum):
    """Terminal outcome of one chat request."""

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CancelOutcome(StrEnum):
    """Result of a cancellation request."""

    CANCELLED = "cancelled"
    NOT_FOUND = "not_found"
    ALREADY_FINISHED = "already_finished"


@dataclass(frozen=True)
class ChatResult:
    """Retained, UI-agnostic result of a chat request.

    ``content`` is the final answer when completed and the preserved
    partial content for failed/cancelled requests.
    """

    request_id: str
    status: ChatStatus
    content: str = ""
    tokens_used: int = 0
    provider: str = ""
    model: str = ""
    error: str = ""


# -- Internal state -----------------------------------------------------------


@dataclass
class _ActiveChat:
    request_id: str
    request: ChatRequest
    provider_override: str | None
    model_override: str | None
    done: asyncio.Future[ChatResult]
    task: asyncio.Task[None] | None = None
    partial: list[str] = field(default_factory=list)
    terminal: bool = False
    result: ChatResult | None = None
    provider: str = ""
    model: str = ""


# -- Service ------------------------------------------------------------------


class ChatService:
    """Streaming chat orchestration for the whole application."""

    def __init__(
        self,
        settings: Settings,
        bus: EventBus,
        router: LLMRouter,
        *,
        lifecycle: "LifecycleManager | None" = None,
        kill_switch: "KillSwitchService | None" = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._settings = settings
        self._bus = bus
        self._router = router
        self._lifecycle = lifecycle
        self._kill_switch = kill_switch
        self._id_factory = id_factory or default_request_id

        self._max_concurrent = int(settings.chat_max_concurrent_generations)
        self._policy = str(settings.chat_concurrency_policy)
        self._cache_size = int(settings.chat_completed_cache_size)
        self._semaphore = asyncio.Semaphore(self._max_concurrent)

        self._active: dict[str, _ActiveChat] = {}
        self._results: OrderedDict[str, ChatResult] = OrderedDict()
        self._request_states: dict[str, AssistantState] = {}
        self._state = AssistantState.IDLE
        self._closed = False

    # -- Public API -----------------------------------------------------------

    @property
    def active_request_ids(self) -> tuple[str, ...]:
        """IDs currently active (generating or queued), in insertion order."""
        return tuple(self._active)

    @property
    def assistant_state(self) -> AssistantState:
        return self._state

    @property
    def concurrency_limit(self) -> int:
        return self._max_concurrent

    async def start_chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        provider: str | None = None,
        model: str | None = None,
        request_id: str | None = None,
    ) -> str:
        """Accept a chat request and return its unique request ID.

        The provider work runs on an owning task; callers observe progress
        through the event bus and/or :meth:`wait`.
        """
        if self._closed:
            raise ChatServiceError("chat service is closed")
        self._ensure_kill_switch_clear()

        rid = self._allocate_request_id(request_id)
        self._ensure_capacity()
        request = ChatRequest(
            model=model,
            messages=list(messages),
            request_id=rid,
        )
        try:
            self._router.validate_request(request)
        except (ProviderError, JarvisError):
            raise
        except Exception as exc:  # noqa: BLE001 - normalize to the core hierarchy
            raise ChatServiceError(
                f"invalid chat request ({type(exc).__name__})"
            ) from exc

        done: asyncio.Future[ChatResult] = asyncio.get_running_loop().create_future()
        active = _ActiveChat(
            request_id=rid,
            request=request,
            provider_override=provider,
            model_override=model,
            done=done,
        )
        self._active[rid] = active
        task = asyncio.create_task(
            self._run(active), name=f"jarvis-chat-{rid}"
        )
        active.task = task
        if self._lifecycle is not None:
            self._lifecycle.register_killable_task(task)
        # Let the owning task take its first step so cancellation/finalization
        # is always observable. A task cancelled before its first step would
        # never run its cleanup (and its done-future would never resolve).
        await asyncio.sleep(0)
        return rid

    async def cancel(self, request_id: str) -> CancelOutcome:
        """Cancel exactly the named request, preserving its partial output.

        The owning task publishes the single ``ChatStreamCancelled`` event;
        an expected task cancellation is never raised to the caller.
        """
        active = self._active.get(request_id)
        if active is None:
            if request_id in self._results:
                return CancelOutcome.ALREADY_FINISHED
            return CancelOutcome.NOT_FOUND
        if active.terminal or active.task is None or active.task.done():
            return CancelOutcome.ALREADY_FINISHED
        active.task.cancel()
        await asyncio.wait([active.task])
        return CancelOutcome.CANCELLED

    async def wait(self, request_id: str) -> ChatResult:
        """Await the terminal result of a request.

        Never raises the expected inner ``CancelledError`` for a cancelled
        generation; unknown IDs raise :class:`ChatRequestNotFoundError`.
        """
        active = self._active.get(request_id)
        if active is not None:
            return await asyncio.shield(active.done)
        result = self._results.get(request_id)
        if result is None:
            raise ChatRequestNotFoundError(
                f"unknown or expired chat request: {request_id!r}"
            )
        return result

    def partial(self, request_id: str) -> str:
        """Content published so far (or preserved terminal content)."""
        active = self._active.get(request_id)
        if active is not None:
            return "".join(active.partial)
        result = self._results.get(request_id)
        return result.content if result is not None else ""

    async def close(self) -> None:
        """Cancel and await every active/queued request (idempotent)."""
        if self._closed and not self._active:
            return
        self._closed = True

        tasks = [
            active.task
            for active in self._active.values()
            if active.task is not None and not active.task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # Defensive cleanup for any task that could not resolve its own future
        # (e.g. cancellation before the coroutine's first step).
        for request_id, active in list(self._active.items()):
            self._active.pop(request_id, None)
            if not active.terminal:
                await self._publish_cancelled(active)
            if active.result is None:  # pragma: no cover - defensive
                active.result = ChatResult(
                    request_id=request_id,
                    status=ChatStatus.CANCELLED,
                    content="".join(active.partial),
                    provider=active.provider,
                    model=active.model,
                    error="chat service closed",
                )
            self._remember(active.result)
            if not active.done.done():
                active.done.set_result(active.result)

        self._request_states.clear()
        await self._publish_state_if_changed()

    # -- Execution ------------------------------------------------------------

    async def _run(self, active: _ActiveChat) -> None:
        request_id = active.request_id
        slot_acquired = False
        try:
            if self._policy == "queue":
                await self._semaphore.acquire()
                slot_acquired = True
            self._ensure_kill_switch_clear()
            await self._set_request_state(request_id, AssistantState.THINKING)
            await self._consume(active)
        except asyncio.CancelledError:
            await self._publish_cancelled(active)
            raise
        except KillSwitchActiveError as exc:
            await self._publish_failed(active, str(exc))
        except ProviderError as exc:
            await self._publish_failed(active, self._safe_error(exc))
        except JarvisError as exc:
            await self._publish_failed(active, self._safe_error(exc))
        except Exception as exc:  # noqa: BLE001 - never leak vendor details
            log.exception("chat request %s failed", request_id)
            await self._publish_failed(
                active, self._safe_error(exc, include_type=True)
            )
        finally:
            if slot_acquired:
                self._semaphore.release()
            try:
                await self._set_request_state(request_id, AssistantState.IDLE)
            except Exception:  # noqa: BLE001 - cleanup must never trap the task
                log.warning(
                    "failed to settle assistant state for %s",
                    request_id,
                    exc_info=True,
                )
            self._active.pop(request_id, None)
            if not active.terminal:
                await self._publish_failed(
                    active, "chat stream ended without a terminal event"
                )
            result = active.result
            if result is None:  # pragma: no cover - defensive
                result = ChatResult(
                    request_id=request_id,
                    status=ChatStatus.FAILED,
                    content="".join(active.partial),
                    error="chat request ended unexpectedly",
                )
                active.result = result
            self._remember(result)
            if not active.done.done():
                active.done.set_result(result)

    async def _consume(self, active: _ActiveChat) -> None:
        request_id = active.request_id
        public_seq = 0
        expected_provider_seq = 0
        routed = False

        stream = self._router.stream_chat(
            active.request,
            provider=active.provider_override,
            model=active.model_override,
        )
        try:
            async for event in stream:
                if isinstance(event, StreamRouted):
                    if event.request_id != request_id:
                        raise ProviderResponseError(
                            "router route metadata request ID mismatch"
                        )
                    if routed:
                        raise ProviderResponseError(
                            "router emitted duplicate route metadata"
                        )
                    routed = True
                    active.provider = event.provider
                    active.model = event.model
                    await self._bus.publish(
                        ChatStreamStarted(
                            request_id=request_id,
                            provider=event.provider,
                            model=event.model,
                            privacy=event.privacy,
                        )
                    )
                    continue

                if event.request_id != request_id:
                    log.warning(
                        "ignoring %s with mismatched request ID for %s",
                        type(event).__name__,
                        request_id,
                    )
                    continue

                if isinstance(event, StreamStart):
                    if not routed:
                        raise ProviderResponseError(
                            "provider stream started without route metadata"
                        )
                    if event.model:
                        active.model = event.model
                    continue

                if isinstance(event, Token):
                    if event.seq != expected_provider_seq:
                        log.warning(
                            "dropping out-of-order provider token for %s "
                            "(seq %s, expected %s)",
                            request_id,
                            event.seq,
                            expected_provider_seq,
                        )
                        continue
                    expected_provider_seq += 1
                    if not event.text:
                        continue  # empty chunks carry no content
                    active.partial.append(event.text)
                    await self._set_request_state(
                        request_id, AssistantState.SPEAKING
                    )
                    await self._bus.publish(
                        ChatToken(
                            request_id=request_id,
                            seq=public_seq,
                            text=event.text,
                        )
                    )
                    public_seq += 1
                    continue

                if isinstance(event, StreamDone):
                    await self._publish_completed(active, event.tokens_used)
                    return

                if isinstance(event, StreamError):
                    await self._publish_failed(
                        active, self._safe_error(event.error)
                    )
                    return

                if isinstance(event, StreamCancelled):
                    await self._publish_cancelled(active)
                    return
        finally:
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                await aclose()

        if not active.terminal:
            await self._publish_failed(
                active, "chat stream ended without a terminal event"
            )

    # -- Terminal event helpers ----------------------------------------------

    async def _publish_completed(
        self, active: _ActiveChat, tokens_used: int
    ) -> None:
        if active.terminal:
            return
        active.terminal = True
        content = "".join(active.partial)
        active.result = ChatResult(
            request_id=active.request_id,
            status=ChatStatus.COMPLETED,
            content=content,
            tokens_used=tokens_used,
            provider=active.provider,
            model=active.model,
        )
        await self._bus.publish(
            ChatStreamCompleted(
                request_id=active.request_id,
                content=content,
                tokens_used=tokens_used,
            )
        )

    async def _publish_failed(self, active: _ActiveChat, detail: str) -> None:
        if active.terminal:
            return
        active.terminal = True
        content = "".join(active.partial)
        detail = (detail or "chat request failed")[:_MAX_FAILURE_DETAIL]
        active.result = ChatResult(
            request_id=active.request_id,
            status=ChatStatus.FAILED,
            content=content,
            provider=active.provider,
            model=active.model,
            error=detail,
        )
        await self._bus.publish(
            ChatStreamFailed(
                request_id=active.request_id,
                detail=detail,
                partial_content=content,
            )
        )

    async def _publish_cancelled(self, active: _ActiveChat) -> None:
        if active.terminal:
            return
        active.terminal = True
        content = "".join(active.partial)
        active.result = ChatResult(
            request_id=active.request_id,
            status=ChatStatus.CANCELLED,
            content=content,
            provider=active.provider,
            model=active.model,
            error="cancelled",
        )
        await self._bus.publish(
            ChatStreamCancelled(
                request_id=active.request_id,
                partial_content=content,
            )
        )

    # -- Assistant state ------------------------------------------------------

    async def _set_request_state(
        self, request_id: str, state: AssistantState
    ) -> None:
        if state is AssistantState.IDLE:
            self._request_states.pop(request_id, None)
        else:
            self._request_states[request_id] = state
        await self._publish_state_if_changed()

    async def _publish_state_if_changed(self) -> None:
        states = set(self._request_states.values())
        if AssistantState.SPEAKING in states:
            aggregate = AssistantState.SPEAKING
        elif AssistantState.THINKING in states:
            aggregate = AssistantState.THINKING
        else:
            aggregate = AssistantState.IDLE
        if aggregate is not self._state:
            self._state = aggregate
            await self._bus.publish(
                AssistantStateChanged(state=aggregate.value)
            )

    # -- Registry / capacity / kill switch ------------------------------------

    def _allocate_request_id(self, explicit: str | None) -> str:
        if explicit is not None:
            if not explicit:
                raise ChatServiceError("request_id must be non-empty")
            if explicit in self._active:
                raise DuplicateRequestIdError(
                    f"chat request ID is already active: {explicit!r}"
                )
            return explicit

        for _ in range(_MAX_ID_ATTEMPTS):
            candidate = self._id_factory()
            if candidate and candidate not in self._active:
                return candidate
        raise ChatServiceError(
            "could not allocate a unique chat request ID"
        )

    def _ensure_capacity(self) -> None:
        if self._policy != "reject":
            return
        if len(self._active) >= self._max_concurrent:
            raise ChatCapacityError(
                f"chat concurrency limit reached ({self._max_concurrent})"
            )

    def _kill_switch_active(self) -> bool:
        if self._kill_switch is not None:
            return bool(self._kill_switch.active)
        if self._lifecycle is not None:
            return bool(self._lifecycle.kill_switch_active)
        return False

    def _ensure_kill_switch_clear(self) -> None:
        if self._kill_switch_active():
            raise KillSwitchActiveError(
                "emergency stop is engaged; chat requests are blocked"
            )

    # -- Misc ----------------------------------------------------------------

    def _remember(self, result: ChatResult) -> None:
        self._results[result.request_id] = result
        self._results.move_to_end(result.request_id)
        while len(self._results) > self._cache_size:
            self._results.popitem(last=False)

    @staticmethod
    def _safe_error(error: object, *, include_type: bool = False) -> str:
        """Credential-free, bounded failure detail for events/results."""
        message = str(error).strip()
        if not message:
            message = "chat request failed"
        if include_type:
            message = f"{type(error).__name__}: {message}"
        return message[:_MAX_FAILURE_DETAIL]


def default_request_id() -> str:
    """Generate a URL/log-safe unique request ID."""
    return uuid.uuid4().hex
