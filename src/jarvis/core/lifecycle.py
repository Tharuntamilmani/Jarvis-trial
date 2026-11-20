"""Application lifecycle manager for JARVIS.

Performs ordered startup, per-service health checks, graceful shutdown, and
coordinates the emergency stop (kill switch). A failing service degrades
startup rather than crashing the process: the failure is published on the
event bus and the service is marked unavailable.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

from jarvis.core.event_bus import EventBus
from jarvis.core.events import (
    KillSwitchRearmed,
    KillSwitchTriggered,
    ServiceStatusChanged,
    ShutdownCompleted,
    StartupCompleted,
)
from jarvis.core.exceptions import KillSwitchActiveError
from jarvis.core.service_registry import ServiceRegistry
from jarvis.monitoring.logging import get_logger

log = get_logger(__name__)


class ServiceStatus(StrEnum):
    STARTING = "starting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    STOPPED = "stopped"


HealthCheck = Callable[[], Awaitable[bool] | bool]


class LifecycleManager:
    """Ordered startup/shutdown and health coordination."""

    def __init__(self, registry: ServiceRegistry, bus: EventBus) -> None:
        self._registry = registry
        self._bus = bus
        self._health_checks: dict[str, HealthCheck] = {}
        self._statuses: dict[str, ServiceStatus] = {}
        self._kill_switch_active = False
        self._kill_tasks: set[asyncio.Task[None]] = set()

    # -- Registration of health checks ---------------------------------------

    def set_health_check(self, service: str, check: HealthCheck) -> None:
        """Attach a health check callable to a registered service."""
        if not self._registry.is_registered(service):
            raise LookupError(f"service not registered: {service!r}")
        self._health_checks[service] = check

    # -- Startup / shutdown ---------------------------------------------------

    async def startup(self) -> None:
        """Resolve every service in registration order and verify health."""
        for name in self._registry.names:
            await self._start_one(name)
        await self._bus.publish(StartupCompleted())
        log.info("startup complete: %s", ", ".join(self._registry.names))

    async def _start_one(self, name: str) -> None:
        await self._set_status(name, ServiceStatus.STARTING)
        try:
            await self._registry.get(name)
        except Exception as exc:  # noqa: BLE001 - degrade, never crash
            log.exception("service %s failed to start", name)
            await self._set_status(
                name, ServiceStatus.UNAVAILABLE, detail=str(exc)
            )
            return
        await self.check_health(name)

    async def check_health(self, name: str) -> ServiceStatus:
        """Run the health check for ``name`` and publish the result."""
        check = self._health_checks.get(name)
        if check is None:
            await self._set_status(name, ServiceStatus.HEALTHY)
            return ServiceStatus.HEALTHY
        try:
            result = check()
            if asyncio.iscoroutine(result):
                result = await result
            status = (
                ServiceStatus.HEALTHY if result else ServiceStatus.UNAVAILABLE
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("health check for %s raised: %s", name, exc)
            status = ServiceStatus.UNAVAILABLE
        await self._set_status(name, status)
        return status

    async def check_all_health(self) -> dict[str, ServiceStatus]:
        """Refresh health for every registered service."""
        for name in self._registry.names:
            await self.check_health(name)
        return dict(self._statuses)

    async def shutdown(self) -> None:
        """Shut down every service in reverse registration order."""
        for name in reversed(self._registry.names):
            entry = self._registry._entries[name]  # noqa: SLF001
            if not entry.resolved:
                continue
            instance = entry.instance
            cleanup = entry.cleanup
            try:
                if cleanup is not None and instance is not None:
                    result = cleanup(instance)
                    if asyncio.iscoroutine(result):
                        await result
                close = getattr(instance, "close", None)
                if close is not None and cleanup is None:
                    result = close()
                    if asyncio.iscoroutine(result):
                        await result
            except Exception:  # noqa: BLE001
                log.exception("cleanup failed for service %s", name)
            await self._set_status(name, ServiceStatus.STOPPED)
        await self._bus.publish(ShutdownCompleted())
        log.info("shutdown complete")

    # -- Kill switch ----------------------------------------------------------

    @property
    def kill_switch_active(self) -> bool:
        return self._kill_switch_active

    def register_killable_task(self, task: asyncio.Task[Any]) -> None:
        """Register a task that the kill switch will cancel."""
        self._kill_tasks.add(task)
        task.add_done_callback(self._kill_tasks.discard)

    async def trigger_kill_switch(self, reason: str = "") -> None:
        """Engage the emergency stop: cancel killable tasks, block sensitive ops."""
        if self._kill_switch_active:
            return
        self._kill_switch_active = True
        log.warning("KILL SWITCH ENGAGED: %s", reason or "(no reason)")
        for task in list(self._kill_tasks):
            if not task.done():
                task.cancel()
        await self._bus.publish(KillSwitchTriggered(reason=reason))

    async def rearm_kill_switch(self) -> None:
        """Disengage the emergency stop (manual re-arm only)."""
        if not self._kill_switch_active:
            return
        self._kill_switch_active = False
        log.info("kill switch re-armed")
        await self._bus.publish(KillSwitchRearmed())

    @contextlib.asynccontextmanager
    async def guard_sensitive(self) -> Any:
        """Context manager that refuses to run while the kill switch is active."""
        if self._kill_switch_active:
            raise KillSwitchActiveError("emergency stop is engaged")
        yield

    # -- Internals ------------------------------------------------------------

    async def _set_status(
        self, service: str, status: ServiceStatus, detail: str = ""
    ) -> None:
        self._statuses[service] = status
        await self._bus.publish(
            ServiceStatusChanged(
                service=service, status=str(status.value), detail=detail
            )
        )

    @property
    def statuses(self) -> dict[str, ServiceStatus]:
        return dict(self._statuses)
