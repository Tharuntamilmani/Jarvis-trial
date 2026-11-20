"""CoreEngine provider-status facade (Stage 2.6, additive)."""

from __future__ import annotations

from jarvis.core.engine import CoreEngine
from jarvis.intelligence.providers.base import (
    PrivacyLevel,
    ProviderCapabilities,
)


class FakeProvider:
    def __init__(
        self,
        provider_id: str,
        privacy: PrivacyLevel,
        *,
        configured: bool = True,
    ) -> None:
        self.id = provider_id
        self.capabilities = ProviderCapabilities(
            streaming=True,
            tools=False,
            context_size=1000,
            privacy=privacy,
        )
        self._configured = configured

    def is_configured(self) -> bool:
        return self._configured


class FakeRegistry:
    def __init__(self, providers, *, failures: set[str] | None = None) -> None:
        self._providers = {provider.id: provider for provider in providers}
        self._health: dict[str, dict] = {}
        self._failures = set(failures or ())
        self.refresh_calls: list[str | None] = []

    @property
    def ids(self):
        return tuple(self._providers)

    def get(self, provider_id: str):
        return self._providers[provider_id]

    def health_snapshot(self):
        return {pid: dict(info) for pid, info in self._health.items()}

    async def refresh_health(self, provider_id=None):
        self.refresh_calls.append(provider_id)
        targets = [provider_id] if provider_id is not None else list(self._providers)
        for pid in targets:
            if pid in self._failures:
                raise RuntimeError("secret internal failure detail")
            provider = self._providers[pid]
            self._health[pid] = {
                "available": True,
                "detail": "connected",
                "privacy": provider.capabilities.privacy.value,
            }
        return dict(self._health)


def _engine_with(registry: FakeRegistry) -> CoreEngine:
    engine = CoreEngine()
    engine.registry.register_instance("providers", registry)
    return engine


def test_cached_snapshot_is_safe_and_configured() -> None:
    registry = FakeRegistry(
        [
            FakeProvider("ollama", PrivacyLevel.LOCAL),
            FakeProvider("groq", PrivacyLevel.CLOUD, configured=False),
        ]
    )
    engine = _engine_with(registry)

    statuses = engine.provider_status_snapshot()

    assert statuses["ollama"].privacy == "local"
    assert statuses["ollama"].configured is True
    assert statuses["ollama"].model == engine.settings.ollama_model
    assert statuses["ollama"].available is False
    assert statuses["ollama"].detail == "not checked"
    assert statuses["groq"].privacy == "cloud"
    assert statuses["groq"].configured is False


async def test_refresh_checks_each_provider_once() -> None:
    registry = FakeRegistry(
        [
            FakeProvider("ollama", PrivacyLevel.LOCAL),
            FakeProvider("groq", PrivacyLevel.CLOUD),
        ]
    )
    engine = _engine_with(registry)

    statuses = await engine.refresh_provider_statuses()

    assert registry.refresh_calls == ["ollama", "groq"]
    assert statuses["ollama"].available is True
    assert statuses["ollama"].detail == "connected"
    assert statuses["groq"].model == engine.settings.groq_model


async def test_refresh_failure_is_isolated_and_never_leaks_detail() -> None:
    registry = FakeRegistry(
        [
            FakeProvider("ollama", PrivacyLevel.LOCAL),
            FakeProvider("groq", PrivacyLevel.CLOUD),
        ],
        failures={"groq"},
    )
    engine = _engine_with(registry)

    statuses = await engine.refresh_provider_statuses()

    assert statuses["ollama"].available is True
    assert statuses["groq"].available is False
    assert statuses["groq"].detail == "health check failed"
    assert "secret" not in statuses["groq"].detail


def test_missing_provider_registry_returns_empty() -> None:
    engine = CoreEngine()
    assert engine.provider_status_snapshot() == {}
