"""Approval service for sensitive actions (Phase 1 + Phase 7.2 payload binding)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import time
from typing import TYPE_CHECKING
import uuid

from jarvis.config import Settings
from jarvis.core.event_bus import EventBus
from jarvis.core.events import ApprovalDecided, ApprovalRequested, KillSwitchTriggered
from jarvis.core.exceptions import ApprovalDeniedError
from jarvis.monitoring.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from jarvis.core.lifecycle import LifecycleManager

log = get_logger(__name__)

ApprovalResponder = Callable[[ApprovalRequested], Awaitable[bool]]


@dataclass
class ApprovalGrant:
    """Single-use, time-limited, exact-payload-bound approval record."""

    request_id: str
    fingerprint: str
    expires_at: float
    used: bool = False


class ApprovalService:
    """Await explicit human approval bound to the exact tool-call payload."""

    def __init__(
        self,
        settings: Settings,
        bus: EventBus,
        *,
        lifecycle: LifecycleManager | None = None,
        grant_ttl_seconds: float = 30.0,
    ) -> None:
        self._settings = settings
        self._bus = bus
        self._lifecycle = lifecycle
        self._grant_ttl_seconds = max(1.0, float(grant_ttl_seconds))
        self._responders: list[ApprovalResponder] = []
        self._pending: dict[str, asyncio.Future[bool]] = {}
        self._grants: dict[str, ApprovalGrant] = {}
        self._bus.subscribe(KillSwitchTriggered, self._on_kill_switch)

    # -- Responder management --------------------------------------------------

    def add_responder(self, responder: ApprovalResponder) -> None:
        self._responders.append(responder)

    def remove_responder(self, responder: ApprovalResponder) -> None:
        if responder in self._responders:
            self._responders.remove(responder)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    # -- Request flow ----------------------------------------------------------

    async def request(
        self,
        action: str,
        summary: str = "",
        *,
        timeout: float | None = None,
        fingerprint: str = "",
        tool_id: str = "",
        risk_level: str = "",
        target: str = "",
        arguments_summary: str = "",
        agent_run_id: str = "",
    ) -> str:
        """Request approval; return ``request_id`` or raise ``ApprovalDeniedError``."""
        if self._lifecycle is not None and self._lifecycle.kill_switch_active:
            raise ApprovalDeniedError(
                f"action {action!r} denied: kill switch is active"
            )
        if not self._responders:
            await self._bus.publish(
                ApprovalDecided(
                    request_id="", approved=False, decided_by="fail_closed_default"
                )
            )
            raise ApprovalDeniedError(
                f"action {action!r} denied: no approval responder available"
            )

        effective_timeout = (
            self._settings.approval_timeout_seconds
            if timeout is None
            else timeout
        )
        request_id = uuid.uuid4().hex
        event = ApprovalRequested(
            request_id=request_id,
            action=action,
            summary=summary,
            tool_id=tool_id or action,
            risk_level=risk_level,
            target=target,
            arguments_summary=arguments_summary,
            agent_run_id=agent_run_id,
            fingerprint=fingerprint,
            timeout_seconds=float(effective_timeout),
        )
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._bus.publish(event)
        try:
            granted = await self._await_decision(event, future, effective_timeout)
        finally:
            self._pending.pop(request_id, None)
        if not granted:
            raise ApprovalDeniedError(f"action {action!r} was not approved")

        self._grants[request_id] = ApprovalGrant(
            request_id=request_id,
            fingerprint=fingerprint,
            expires_at=time.monotonic() + self._grant_ttl_seconds,
        )
        return request_id

    def verify_and_consume(
        self, request_id: str, expected_fingerprint: str
    ) -> bool:
        """Verify single-use, unexpired, exact-payload grant and consume it."""
        if self._lifecycle is not None and self._lifecycle.kill_switch_active:
            self._grants.clear()
            return False
        grant = self._grants.get(request_id)
        if grant is None or grant.used:
            return False
        if time.monotonic() > grant.expires_at:
            self._grants.pop(request_id, None)
            return False
        if grant.fingerprint != expected_fingerprint:
            return False
        grant.used = True
        return True

    async def _await_decision(
        self,
        event: ApprovalRequested,
        future: asyncio.Future[bool],
        timeout: float,
    ) -> bool:
        try:
            granted = await asyncio.wait_for(
                self._collect_responses(event, future), timeout=timeout
            )
            decided_by = "user"
        except TimeoutError:
            granted = False
            decided_by = "timeout"
        await self._bus.publish(
            ApprovalDecided(
                request_id=event.request_id,
                approved=granted,
                decided_by=decided_by,
            )
        )
        return granted

    async def _collect_responses(
        self, event: ApprovalRequested, future: asyncio.Future[bool]
    ) -> bool:
        tasks = [
            asyncio.ensure_future(responder(event))
            for responder in list(self._responders)
        ]
        if not tasks:
            return False
        try:
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            for task in done:
                if task.done() and not task.cancelled():
                    result = task.result()
                    if result:
                        if not future.done():
                            future.set_result(True)
                        return True
            return False
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()

    def resolve_pending(self, request_id: str, approved: bool) -> bool:
        """Allow a UI to resolve a pending request directly."""
        future = self._pending.get(request_id)
        if future is None or future.done():
            return False
        future.set_result(approved)
        return True

    async def _on_kill_switch(self, event: KillSwitchTriggered) -> None:
        del event
        self.close()

    def close(self) -> None:
        """Fail all pending requests and invalidate unused grants."""
        for future in list(self._pending.values()):
            if not future.done():
                future.set_result(False)
        self._pending.clear()
        self._grants.clear()
