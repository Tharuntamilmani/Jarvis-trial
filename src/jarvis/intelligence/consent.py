"""Cloud consent manager (Stage 2.4).

Every dispatch to a cloud provider requires an explicit, request-specific
consent decision bound to the exact sanitized payload fingerprint.

Fail-closed semantics (Plan v1.1):

- Default decision is DENIED.
- No consent is ever inferred from configuration, previous requests, or
  provider preferences.
- Missing responder, timeout, and invalid/mismatched resolutions all deny.
- A decision is valid ONLY for its request ID + payload fingerprint; a
  decision for another request is rejected.
- No persistent consent in this stage; no expanded-history consent.

The manager is UI-agnostic: a future UI connects through
:meth:`set_responder` (async callback receiving only safe proposal
metadata) or resolves out-of-band via :meth:`resolve`. No PySide6.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from jarvis.config import Settings
from jarvis.core.event_bus import EventBus
from jarvis.core.events import CloudConsentDecided, CloudFallbackProposed
from jarvis.monitoring.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from jarvis.intelligence.cloud_payload import CloudPayload

log = get_logger(__name__)

_MAX_KEPT_DECISIONS = 256

ConsentResponder = Callable[["CloudFallbackProposed", "CloudPayload"], Awaitable[bool]]


@dataclass(frozen=True)
class ConsentDecision:
    """Outcome of one consent request, bound to request + payload."""

    request_id: str
    granted: bool
    fingerprint: str
    decided_by: str  # user | timeout | fail_closed


@dataclass
class _PendingConsent:
    future: asyncio.Future[bool]
    fingerprint: str
    responder_failed: bool = False


class ConsentManager:
    """Explicit, per-request cloud consent with fail-closed defaults."""

    def __init__(self, settings: Settings, bus: EventBus) -> None:
        self._bus = bus
        self._timeout = getattr(settings, "cloud_consent_timeout_seconds", 30.0)
        self._responder: ConsentResponder | None = None
        self._pending: dict[str, _PendingConsent] = {}
        self._decisions: dict[str, ConsentDecision] = {}

    # -- Responder wiring (UI connects in Stage 2.6) ----------------------------

    def set_responder(self, responder: ConsentResponder) -> None:
        self._responder = responder

    def clear_responder(self) -> None:
        self._responder = None

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    # -- Request flow -------------------------------------------------------------

    async def request_consent(
        self,
        *,
        request_id: str,
        proposal: CloudFallbackProposed,
        payload: "CloudPayload",
        timeout: float | None = None,
    ) -> ConsentDecision:
        """Ask for cloud consent; DENY on rejection, timeout, error, or
        absence of any responder."""
        fingerprint = payload.fingerprint
        if not request_id:
            # A decision that cannot be bound to a request is never usable.
            return await self._finalize(
                request_id, fingerprint, False, "fail_closed"
            )
        if request_id in self._pending:
            # Duplicate concurrent request IDs must not overwrite each
            # other's pending decision; fail the newcomer closed.
            log.warning("duplicate consent request id %s; denying", request_id)
            return await self._finalize(
                request_id, fingerprint, False, "fail_closed"
            )

        responder = self._responder
        if responder is None:
            decision = await self._finalize(
                request_id, fingerprint, False, "fail_closed"
            )
            return decision

        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        pending = _PendingConsent(future=future, fingerprint=fingerprint)
        self._pending[request_id] = pending
        task = asyncio.ensure_future(responder(proposal, payload))
        task.add_done_callback(self._on_responder_done(pending))

        effective_timeout = self._timeout if timeout is None else timeout
        granted = False
        decided_by = "timeout"
        try:
            granted = await asyncio.wait_for(future, timeout=effective_timeout)
            decided_by = "fail_closed" if pending.responder_failed else "user"
        except asyncio.TimeoutError:
            granted = False
            decided_by = "timeout"
        finally:
            self._pending.pop(request_id, None)
            if not task.done():
                task.cancel()

        decision = await self._finalize(request_id, fingerprint, granted, decided_by)
        return decision

    def _on_responder_done(self, pending: _PendingConsent):
        def _callback(task: asyncio.Task[bool]) -> None:
            if pending.future.done():
                return
            try:
                granted = bool(task.result())
            except asyncio.CancelledError:
                pending.responder_failed = True
                granted = False
            except Exception:  # noqa: BLE001 - responder failure denies
                log.warning("consent responder failed; denying", exc_info=True)
                pending.responder_failed = True
                granted = False
            if not pending.future.done():
                pending.future.set_result(granted)

        return _callback

    # -- Out-of-band resolution (future UI path) ------------------------------------

    def resolve(self, request_id: str, granted: bool, fingerprint: str) -> bool:
        """Deliver a UI decision for a pending request.

        Returns True when the decision was accepted. Decisions for unknown
        request IDs or mismatched fingerprints are REJECTED (pending stays
        until its timeout denies).
        """
        pending = self._pending.get(request_id)
        if pending is None or pending.future.done():
            return False
        if fingerprint != pending.fingerprint:
            log.warning("consent fingerprint mismatch for %s; rejected", request_id)
            return False
        pending.future.set_result(granted)
        return True

    def get_decision(self, request_id: str) -> ConsentDecision | None:
        return self._decisions.get(request_id)

    # -- Internals ---------------------------------------------------------------------

    async def _finalize(
        self, request_id: str, fingerprint: str, granted: bool, decided_by: str
    ) -> ConsentDecision:
        decision = ConsentDecision(
            request_id=request_id,
            granted=granted,
            fingerprint=fingerprint,
            decided_by=decided_by,
        )
        self._decisions[request_id] = decision
        while len(self._decisions) > _MAX_KEPT_DECISIONS:
            self._decisions.pop(next(iter(self._decisions)))
        await self._bus.publish(
            CloudConsentDecided(
                request_id=request_id,
                granted=granted,
                decided_by=decided_by,
                fingerprint=fingerprint,
            )
        )
        return decision
