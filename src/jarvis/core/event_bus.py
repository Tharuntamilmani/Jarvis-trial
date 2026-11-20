"""Asynchronous event bus for JARVIS.

Publish/subscribe with typed events. Subscriber exceptions are logged and
isolated — a failing handler can never break the bus or other subscribers.
"""

from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TypeVar

from jarvis.core.events import Event, _to_snake_case
from jarvis.monitoring.logging import get_logger

log = get_logger(__name__)

EventHandler = Callable[[Event], Awaitable[None] | None]
E = TypeVar("E", bound=Event)


def _topic_of(event_type: type[E] | str) -> str:
    """Topic name for an event type or raw topic string.

    Note: ``Event.name`` is an instance property; on the class it would
    return the property object, so classes are converted explicitly.
    """
    if isinstance(event_type, str):
        return event_type
    return _to_snake_case(event_type.__name__)


class EventBus:
    """Async in-process event bus."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventHandler]] = defaultdict(list)
        self._lock = asyncio.Lock()

    # -- Subscription -------------------------------------------------------

    def subscribe(self, event_type: type[E] | str, handler: EventHandler) -> None:
        """Register ``handler`` for an event type (or a raw topic name)."""
        topic = _topic_of(event_type)
        self._subscribers[topic].append(handler)

    def unsubscribe(self, event_type: type[E] | str, handler: EventHandler) -> None:
        """Remove a previously registered handler (no-op if absent)."""
        topic = _topic_of(event_type)
        try:
            self._subscribers[topic].remove(handler)
        except ValueError:
            pass

    # -- Publishing ---------------------------------------------------------

    async def publish(self, event: Event) -> None:
        """Publish an event to all subscribers of its topic."""
        handlers = list(self._subscribers.get(event.name, ()))
        if not handlers:
            return
        results = await asyncio.gather(
            *(_dispatch(handler, event) for handler in handlers),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                log.error(
                    "event handler failed for %s: %s",
                    event.name,
                    result,
                    exc_info=result if isinstance(result, Exception) else None,
                )

    # -- Introspection (tests/diagnostics) -----------------------------------

    def subscriber_count(self, event_type: type[E] | str) -> int:
        topic = _topic_of(event_type)
        return len(self._subscribers.get(topic, ()))

    async def wait_closed(self) -> None:  # symmetry / future use
        return None


async def _dispatch(handler: EventHandler, event: Event) -> None:
    result = handler(event)
    if inspect.isawaitable(result):
        await result
