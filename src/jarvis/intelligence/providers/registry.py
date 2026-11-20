"""Provider registry: discovery, health snapshots.

Routing/failover logic lives in ``jarvis.intelligence.router`` (Stage
2.4); this module registers providers, exposes their health for the status
UI, and closes their HTTP clients at shutdown.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from jarvis.config import Settings
from jarvis.core.event_bus import EventBus
from jarvis.core.events import ProviderHealthChanged
from jarvis.intelligence.providers.base import LLMProvider, ProviderHealth
from jarvis.intelligence.providers.errors import ProviderError
from jarvis.intelligence.providers.groq_provider import GroqProvider
from jarvis.intelligence.providers.ollama_provider import OllamaProvider
from jarvis.monitoring.logging import get_logger

log = get_logger(__name__)


class ProviderRegistry:
    """Registered AI providers and their live health."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._providers: dict[str, LLMProvider] = {}
        self._health: dict[str, ProviderHealth] = {}
        self._health_at: dict[str, float] = {}

    @classmethod
    def create_default(cls, settings: Settings, bus: EventBus) -> "ProviderRegistry":
        registry = cls(bus)
        registry.register(OllamaProvider(settings))
        registry.register(GroqProvider(settings))
        return registry

    # -- Registration -----------------------------------------------------------

    def register(self, provider: LLMProvider) -> None:
        self._providers[provider.id] = provider

    def get(self, provider_id: str) -> LLMProvider:
        if provider_id not in self._providers:
            raise ProviderError(f"provider not registered: {provider_id!r}")
        return self._providers[provider_id]

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(self._providers)

    async def close(self) -> None:
        """Close every provider's resources (lifecycle shutdown)."""
        for provider in self._providers.values():
            aclose = getattr(provider, "aclose", None)
            if aclose is None:
                continue
            result = aclose()
            if asyncio.iscoroutine(result):
                await result

    # -- Health -------------------------------------------------------------------

    async def refresh_health(
        self, provider_id: str | None = None
    ) -> dict[str, ProviderHealth]:
        """Query one provider (or all) and publish changes.

        Targeted refresh (``provider_id`` given) lets the router refresh a
        single provider after a failure without generating health traffic
        for the others.
        """
        targets = (
            {provider_id: self._providers[provider_id]}
            if provider_id is not None
            else self._providers
        )
        for pid, provider in targets.items():
            health = await provider.health()
            previous = self._health.get(pid)
            self._health[pid] = health
            self._health_at[pid] = time.monotonic()
            if (
                previous is None
                or previous.available != health.available
                or previous.detail != health.detail
            ):
                await self._bus.publish(
                    ProviderHealthChanged(
                        provider=pid,
                        available=health.available,
                        detail=health.detail,
                    )
                )
        return dict(self._health)

    def cached_health(self, provider_id: str) -> tuple[ProviderHealth | None, float]:
        """Last known health and its age in seconds (inf if never checked).

        Primarily for UI status and the router's narrow fast-path policy;
        stale health must never suppress a real request attempt.
        """
        health = self._health.get(provider_id)
        checked_at = self._health_at.get(provider_id)
        age = float("inf") if checked_at is None else time.monotonic() - checked_at
        return health, age

    def health_snapshot(self) -> dict[str, Any]:
        """Last known health without re-querying (for status UI)."""
        return {
            provider_id: {
                "available": health.available,
                "detail": health.detail,
                "privacy": str(provider.capabilities.privacy.value),
            }
            for provider_id, health in self._health.items()
            for provider in [self._providers[provider_id]]
        }
