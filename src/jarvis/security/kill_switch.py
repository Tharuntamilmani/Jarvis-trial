"""Emergency stop (kill switch) service.

Thin service wrapper over :class:`jarvis.core.lifecycle.LifecycleManager`'s
kill-switch primitives. Exposes trigger/re-arm with event-bus broadcast and
a request guard that callers can poll. UI entry points (tray, hotkey) call
this service via the core facade.
"""

from __future__ import annotations

from jarvis.config import Settings
from jarvis.core.event_bus import EventBus
from jarvis.core.lifecycle import LifecycleManager
from jarvis.monitoring.logging import get_logger

log = get_logger(__name__)


class KillSwitchService:
    """Emergency stop coordination."""

    def __init__(
        self, settings: Settings, bus: EventBus, lifecycle: LifecycleManager
    ) -> None:
        self._settings = settings
        self._bus = bus
        self._lifecycle = lifecycle

    @property
    def active(self) -> bool:
        return self._lifecycle.kill_switch_active

    async def trigger(self, reason: str = "") -> None:
        """Engage the emergency stop."""
        await self._lifecycle.trigger_kill_switch(reason)

    async def rearm(self) -> None:
        """Disengage the emergency stop (manual action only)."""
        await self._lifecycle.rearm_kill_switch()

    async def guard_ok(self) -> bool:
        """True when sensitive operations may proceed."""
        return not self.active
