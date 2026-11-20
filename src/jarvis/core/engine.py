"""Core engine for JARVIS.

Assembles the core services (bus, registry, lifecycle, security, providers,
database) and exposes the facade the desktop layer uses. This module — and
everything it imports — must NEVER import PySide6.
"""

from __future__ import annotations

import asyncio
import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from jarvis.config import Settings, get_settings
from jarvis.core.event_bus import EventBus
from jarvis.core.lifecycle import LifecycleManager, ServiceStatus
from jarvis.core.service_registry import ServiceRegistry
from jarvis.monitoring.logging import get_logger, setup_logging

if TYPE_CHECKING:  # pragma: no cover - typing only
    from jarvis.database.connection import DatabaseService
    from jarvis.intelligence.providers.registry import ProviderRegistry

log = get_logger(__name__)


@dataclass(frozen=True)
class ProviderStatus:
    """Safe, UI-facing provider status (never contains credentials).

    ``detail`` is a bounded human-readable health string; internal
    exception text is never copied here.
    """

    provider: str
    available: bool = False
    detail: str = "not checked"
    privacy: str = ""  # local | cloud
    configured: bool = False
    model: str = ""


class CoreEngine:
    """Facade over the core service graph."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        setup_logging(level=self.settings.log_level, log_dir=self.settings.log_dir)
        self.bus = EventBus()
        self.registry = ServiceRegistry()
        self.lifecycle = LifecycleManager(self.registry, self.bus)
        self._started = False
        self._startup_task: asyncio.Task[None] | None = None

    # -- Assembly -------------------------------------------------------------

    def assemble(self) -> None:
        """Register all Phase 1 core services (idempotent)."""
        if self.registry.names:
            return
        from jarvis.database.connection import DatabaseService
        from jarvis.intelligence.providers.registry import ProviderRegistry
        from jarvis.security.approval import ApprovalService
        from jarvis.security.audit import AuditService
        from jarvis.security.allowlist import CommandAllowlist
        from jarvis.security.credentials import CredentialStore
        from jarvis.security.kill_switch import KillSwitchService
        from jarvis.security.permissions import PermissionPolicy

        settings = self.settings

        self.registry.register(
            "database",
            lambda: DatabaseService(settings, self.bus),
            cleanup=lambda svc: svc.close(),
        )
        self.registry.register(
            "providers",
            lambda: ProviderRegistry.create_default(settings, self.bus),
            cleanup=lambda reg: reg.close(),
        )
        self.registry.register(
            "credentials", lambda: CredentialStore(settings)
        )
        self.registry.register(
            "audit", lambda: AuditService(settings, self.bus)
        )
        self.registry.register(
            "permissions", lambda: PermissionPolicy.default_policy()
        )
        self.registry.register("allowlist", lambda: CommandAllowlist())
        self.registry.register(
            "approval",
            lambda: ApprovalService(settings, self.bus),
            cleanup=lambda svc: svc.close(),
        )
        self.registry.register(
            "kill_switch",
            lambda: KillSwitchService(settings, self.bus, self.lifecycle),
        )
        from jarvis.intelligence.consent import ConsentManager

        self.registry.register(
            "consent", lambda: ConsentManager(settings, self.bus)
        )
        from jarvis.intelligence.router import LLMRouter

        async def _build_router():
            providers = await self.registry.get("providers")
            consent = await self.registry.get("consent")
            audit = await self.registry.get("audit")
            kill_switch = await self.registry.get("kill_switch")
            return LLMRouter(
                settings=settings,
                providers=providers,
                bus=self.bus,
                consent=consent,
                audit=audit,
                lifecycle=self.lifecycle,
                kill_switch=kill_switch,
            )

        self.registry.register("router", _build_router)

        from jarvis.intelligence.chat_service import ChatService

        async def _build_chat_service():
            router = await self.registry.get("router")
            kill_switch = await self.registry.get("kill_switch")
            return ChatService(
                settings,
                self.bus,
                router,
                lifecycle=self.lifecycle,
                kill_switch=kill_switch,
            )

        self.registry.register(
            "chat",
            _build_chat_service,
            cleanup=lambda svc: svc.close(),
        )

        self.lifecycle.set_health_check(
            "database", lambda: self._database_health()
        )

    async def _database_health(self) -> bool:
        db: DatabaseService | None = self.registry.try_get("database")
        if db is None:
            return False
        return await db.health_check()

    # -- Lifecycle -------------------------------------------------------------

    async def start(self) -> None:
        """Start the core engine (assemble + lifecycle startup)."""
        if self._started:
            return
        self.assemble()
        await self.lifecycle.startup()
        self._started = True
        log.info("core engine started")

    async def shutdown(self) -> None:
        """Graceful shutdown of the core engine."""
        if not self._started:
            return
        await self.lifecycle.shutdown()
        self._started = False
        log.info("core engine stopped")

    # -- Facade used by the desktop layer --------------------------------------

    async def service_statuses(self) -> dict[str, str]:
        await self.lifecycle.check_all_health()
        return {
            name: str(status.value)
            for name, status in self.lifecycle.statuses.items()
        }

    async def trigger_kill_switch(self, reason: str = "") -> None:
        await self.lifecycle.trigger_kill_switch(reason)

    async def rearm_kill_switch(self) -> None:
        await self.lifecycle.rearm_kill_switch()

    @property
    def kill_switch_active(self) -> bool:
        return self.lifecycle.kill_switch_active

    async def provider_health(self) -> dict[str, Any]:
        providers: ProviderRegistry | None = self.registry.try_get("providers")
        if providers is None:
            return {}
        return providers.health_snapshot()

    # -- Provider status facade (Stage 2.6) -----------------------------------

    def provider_status_snapshot(self) -> dict[str, ProviderStatus]:
        """Cached, secret-free provider status. Never performs network I/O."""
        providers = self.registry.try_get("providers")
        if providers is None:
            return {}
        try:
            health = providers.health_snapshot()
        except Exception:  # noqa: BLE001 - status display must never crash
            log.warning("provider health snapshot failed", exc_info=True)
            health = {}

        statuses: dict[str, ProviderStatus] = {}
        for provider_id in getattr(providers, "ids", ()):
            try:
                provider = providers.get(provider_id)
            except Exception:  # noqa: BLE001 - skip unusable entries
                continue
            statuses[provider_id] = self._provider_status(
                provider_id, provider, health.get(provider_id)
            )
        return statuses

    async def refresh_provider_statuses(self) -> dict[str, ProviderStatus]:
        """Run one real health check per provider, then return safe status.

        Failures are isolated per provider and degraded to a generic,
        credential-free detail; stale health is never reported as fresh.
        """
        providers = self.registry.try_get("providers")
        if providers is None:
            return {}

        failed: set[str] = set()
        for provider_id in getattr(providers, "ids", ()):
            try:
                await providers.refresh_health(provider_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - never leak exception text
                failed.add(provider_id)
                log.warning(
                    "provider health check failed for %s", provider_id
                )

        statuses = self.provider_status_snapshot()
        for provider_id in failed:
            current = statuses.get(provider_id)
            if current is not None:
                statuses[provider_id] = dataclasses.replace(
                    current, available=False, detail="health check failed"
                )
        return statuses

    def _provider_status(
        self,
        provider_id: str,
        provider: Any,
        health_info: dict[str, Any] | None,
    ) -> ProviderStatus:
        privacy = ""
        capabilities = getattr(provider, "capabilities", None)
        if capabilities is not None:
            raw_privacy = getattr(capabilities, "privacy", "")
            privacy = getattr(raw_privacy, "value", raw_privacy) or ""

        configured = False
        try:
            configured = bool(provider.is_configured())
        except Exception:  # noqa: BLE001 - a probe failure is "not configured"
            configured = False

        if health_info is None:
            available = False
            detail = "not checked"
        else:
            available = bool(health_info.get("available"))
            detail = str(
                health_info.get("detail") or ("available" if available else "unavailable")
            )
            privacy = str(health_info.get("privacy") or privacy)

        return ProviderStatus(
            provider=provider_id,
            available=available,
            detail=detail[:120],
            privacy=privacy,
            configured=configured,
            model=self._configured_model(provider_id),
        )

    def _configured_model(self, provider_id: str) -> str:
        if provider_id == "ollama":
            return str(self.settings.ollama_model)
        if provider_id == "groq":
            return str(self.settings.groq_model)
        return ""

    @property
    def is_started(self) -> bool:
        return self._started

    @property
    def status_summary(self) -> dict[str, str]:
        return {
            name: str(status.value)
            for name, status in self.lifecycle.statuses.items()
        }

    @staticmethod
    def healthy(status: str) -> bool:
        return status == str(ServiceStatus.HEALTHY.value)
