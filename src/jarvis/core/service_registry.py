"""Service registry for JARVIS.

Services are registered as factories (sync or async) or instances and are
resolved lazily on first access. Registration order defines the lifecycle
startup/shutdown order (see :mod:`jarvis.core.lifecycle`).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from jarvis.core.exceptions import ServiceNotRegisteredError, ServiceError
from jarvis.monitoring.logging import get_logger

log = get_logger(__name__)

T = TypeVar("T")

ServiceFactory = Callable[[], T | Awaitable[T]]


@dataclass
class ServiceEntry:
    name: str
    factory: ServiceFactory | None
    instance: Any = None
    resolved: bool = False
    shutting_down: bool = False
    cleanup: Callable[[Any], Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ServiceRegistry:
    """Lazy service container with deterministic ordering."""

    def __init__(self) -> None:
        self._entries: dict[str, ServiceEntry] = {}
        self._order: list[str] = []

    # -- Registration --------------------------------------------------------

    def register(
        self,
        name: str,
        factory: ServiceFactory,
        *,
        cleanup: Callable[[Any], Any] | None = None,
        replace: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Register a service factory under ``name``.

        ``cleanup(instance)`` is invoked (awaited if awaitable) on shutdown.
        """
        if not name:
            raise ValueError("service name must be non-empty")
        if name in self._entries and not replace:
            raise ServiceError(f"service already registered: {name!r}")
        entry = ServiceEntry(
            name=name,
            factory=factory,
            cleanup=cleanup,
            metadata=dict(metadata or {}),
        )
        if name in self._entries:
            self._entries[name] = entry
        else:
            self._entries[name] = entry
            self._order.append(name)
        log.debug("registered service: %s", name)

    def register_instance(
        self,
        name: str,
        instance: Any,
        *,
        cleanup: Callable[[Any], Any] | None = None,
        replace: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Register an already-constructed service instance."""
        self.register(
            name,
            lambda: instance,
            cleanup=cleanup,
            replace=replace,
            metadata=metadata,
        )
        self._entries[name].instance = instance
        self._entries[name].resolved = True

    # -- Resolution ----------------------------------------------------------

    async def get(self, name: str) -> Any:
        """Resolve a service, constructing it lazily on first access."""
        entry = self._entries.get(name)
        if entry is None:
            raise ServiceNotRegisteredError(f"service not registered: {name!r}")
        if not entry.resolved:
            instance = entry.factory() if entry.factory else None
            if asyncio.iscoroutine(instance) or isinstance(
                instance, AsyncIterator
            ):
                instance = await instance
            if instance is None and entry.factory is None:
                raise ServiceError(f"service {name!r} has no factory")
            entry.instance = instance
            entry.resolved = True
            log.debug("resolved service: %s", name)
        return entry.instance

    def get_sync(self, name: str) -> Any:
        """Return an already-resolved service (no lazy construction)."""
        entry = self._entries.get(name)
        if entry is None:
            raise ServiceNotRegisteredError(f"service not registered: {name!r}")
        if not entry.resolved:
            raise ServiceError(f"service {name!r} not yet resolved")
        return entry.instance

    def try_get(self, name: str) -> Any | None:
        """Return the resolved instance or ``None`` (never raises)."""
        entry = self._entries.get(name)
        return entry.instance if entry and entry.resolved else None

    # -- Introspection -------------------------------------------------------

    @property
    def names(self) -> tuple[str, ...]:
        """Service names in registration order."""
        return tuple(self._order)

    def is_registered(self, name: str) -> bool:
        return name in self._entries

    def is_resolved(self, name: str) -> bool:
        entry = self._entries.get(name)
        return bool(entry and entry.resolved)

    def metadata(self, name: str) -> dict[str, Any]:
        entry = self._entries.get(name)
        if entry is None:
            raise ServiceNotRegisteredError(f"service not registered: {name!r}")
        return dict(entry.metadata)
