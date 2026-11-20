"""Approval service for sensitive actions.

Fail-closed design: a sensitive action must receive an explicit approval
decision. In Phase 1 there is no UI consent card yet, so every request
**auto-denies** after the configured timeout (or immediately when no
responder is connected). This guarantees the security posture before tools
exist. Phase 7 connects real UI responders.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable

from jarvis.config import Settings
from jarvis.core.event_bus import EventBus
from jarvis.core.events import ApprovalDecided, ApprovalRequested
from jarvis.core.exceptions import ApprovalDeniedError
from jarvis.monitoring.logging import get_logger

log = get_logger(__name__)

ApprovalResponder = Callable[[ApprovalRequested], Awaitable[bool]]


class ApprovalService:
    """Await explicit human approval for sensitive actions."""

    def __init__(self, settings: Settings, bus: EventBus) -> None:
        self._settings = settings
        self._bus = bus
        self._responders: list[ApprovalResponder] = []
        self._pending: dict[str, asyncio.Future[bool]] = {}

    # -- Responder management (UI connects in Phase 7) ------------------------

    def add_responder(self, responder: ApprovalResponder) -> None:
        self._responders.append(responder)

    def remove_responder(self, responder: ApprovalResponder) -> None:
        with_context = list(self._responders)
        if responder in with_context:
            self._responders.remove(responder)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    # -- Request flow ----------------------------------------------------------

    async def request(
        self, action: str, summary: str = "", *, timeout: float | None = None
    ) -> None:
        """Request approval; raise :class:`ApprovalDeniedError` if not granted.

        With responders connected, the first to answer decides. Without any,
        the request fails closed immediately (no UI exists to ask).
        """
        if not self._responders:
            await self._bus.publish(
                ApprovalDecided(
                    request_id="", approved=False, decided_by="fail_closed_default"
                )
            )
            raise ApprovalDeniedError(
                f"action {action!r} denied: no approval responder available"
            )

        request_id = uuid.uuid4().hex
        event = ApprovalRequested(
            request_id=request_id, action=action, summary=summary
        )
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._bus.publish(event)
        try:
            granted = await self._await_decision(event, future, timeout)
        finally:
            self._pending.pop(request_id, None)
        if not granted:
            raise ApprovalDeniedError(f"action {action!r} was not approved")

    async def _await_decision(
        self,
        event: ApprovalRequested,
        future: asyncio.Future[bool],
        timeout: float | None,
    ) -> bool:
        timeout = (
            self._settings.approval_timeout_seconds
            if timeout is None
            else timeout
        )
        try:
            granted = await asyncio.wait_for(
                self._collect_responses(event, future), timeout=timeout
            )
            decided_by = "user"
        except asyncio.TimeoutError:
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

    def close(self) -> None:
        """Fail all pending requests (used at shutdown)."""
        for future in list(self._pending.values()):
            if not future.done():
                future.set_result(False)
        self._pending.clear()
