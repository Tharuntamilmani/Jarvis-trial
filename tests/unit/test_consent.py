"""Cloud consent manager tests (Stage 2.4).

Consent is explicit, per-request, bound to the sanitized payload
fingerprint, and fail-closed on absence, rejection, timeout, error, empty
request IDs, duplicate request IDs, and mismatched bindings.
"""

from __future__ import annotations

import asyncio
import dataclasses

import pytest

from jarvis.config import Settings
from jarvis.core.event_bus import EventBus
from jarvis.core.events import CloudConsentDecided, CloudFallbackProposed
from jarvis.intelligence.cloud_payload import build_cloud_payload
from jarvis.intelligence.consent import ConsentManager
from jarvis.intelligence.prompts import JARVIS_SYSTEM_PROMPT
from jarvis.intelligence.providers.base import ChatMessage, ChatRequest


def _payload(request_id: str = "req-c1", user: str = "hello"):
    request = ChatRequest(
        model="qwen3:8b",
        messages=[ChatMessage("user", user)],
        request_id=request_id,
    )
    return build_cloud_payload(request, system_prompt=JARVIS_SYSTEM_PROMPT)


def _proposal(payload) -> CloudFallbackProposed:
    return CloudFallbackProposed(
        request_id=payload.request_id,
        reason="local unavailable",
        provider="groq",
        model="",
        payload_preview=payload.preview(),
        payload_fingerprint=payload.fingerprint,
        scope=payload.scope_description,
    )


def _manager(settings: Settings, bus: EventBus) -> ConsentManager:
    return ConsentManager(settings, bus)


async def _wait_pending(manager: ConsentManager) -> None:
    for _ in range(100):
        if manager.pending_count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("consent request never became pending")


@pytest.mark.asyncio
async def test_default_denied_without_responder(settings) -> None:
    bus = EventBus()
    manager = _manager(settings, bus)
    payload = _payload()
    decided: list[CloudConsentDecided] = []
    bus.subscribe(CloudConsentDecided, decided.append)

    decision = await manager.request_consent(
        request_id=payload.request_id,
        proposal=_proposal(payload),
        payload=payload,
    )

    assert decision.granted is False
    assert decision.decided_by == "fail_closed"
    assert decision.request_id == payload.request_id
    assert decision.fingerprint == payload.fingerprint
    assert [e.granted for e in decided] == [False]
    assert manager.pending_count == 0


@pytest.mark.asyncio
async def test_responder_approves(settings) -> None:
    bus = EventBus()
    manager = _manager(settings, bus)
    payload = _payload()
    seen: list[CloudFallbackProposed] = []

    async def responder(proposal, received_payload) -> bool:
        seen.append(proposal)
        assert received_payload.fingerprint == proposal.payload_fingerprint
        return True

    manager.set_responder(responder)
    decision = await manager.request_consent(
        request_id=payload.request_id,
        proposal=_proposal(payload),
        payload=payload,
    )

    assert decision.granted is True
    assert decision.decided_by == "user"
    assert seen and seen[0].request_id == payload.request_id
    assert manager.get_decision(payload.request_id) == decision


@pytest.mark.asyncio
async def test_responder_rejects(settings) -> None:
    bus = EventBus()
    manager = _manager(settings, bus)
    payload = _payload()

    async def responder(proposal, received_payload) -> bool:
        return False

    manager.set_responder(responder)
    decision = await manager.request_consent(
        request_id=payload.request_id,
        proposal=_proposal(payload),
        payload=payload,
    )
    assert decision.granted is False
    assert decision.decided_by == "user"


@pytest.mark.asyncio
async def test_timeout_denies_and_clears_pending(settings) -> None:
    bus = EventBus()
    manager = _manager(settings, bus)
    payload = _payload()
    cancelled = asyncio.Event()

    async def slow(proposal, received_payload) -> bool:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return True

    manager.set_responder(slow)
    decision = await manager.request_consent(
        request_id=payload.request_id,
        proposal=_proposal(payload),
        payload=payload,
        timeout=0.05,
    )

    assert decision.granted is False
    assert decision.decided_by == "timeout"
    assert manager.pending_count == 0
    await asyncio.sleep(0)
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_responder_exception_denies(settings) -> None:
    bus = EventBus()
    manager = _manager(settings, bus)
    payload = _payload()

    async def broken(proposal, received_payload) -> bool:
        raise RuntimeError("UI crashed")

    manager.set_responder(broken)
    decision = await manager.request_consent(
        request_id=payload.request_id,
        proposal=_proposal(payload),
        payload=payload,
    )
    assert decision.granted is False
    assert decision.decided_by == "fail_closed"  # responder failure denies


@pytest.mark.asyncio
async def test_resolution_bound_to_request_id(settings) -> None:
    bus = EventBus()
    manager = _manager(settings, bus)
    payload = _payload()
    hold = asyncio.Event()

    async def hanging(proposal, received_payload) -> bool:
        await hold.wait()
        return False

    manager.set_responder(hanging)
    task = asyncio.ensure_future(
        manager.request_consent(
            request_id=payload.request_id,
            proposal=_proposal(payload),
            payload=payload,
            timeout=5,
        )
    )
    await _wait_pending(manager)

    assert manager.resolve("req-other", True, payload.fingerprint) is False
    assert manager.resolve(payload.request_id, True, payload.fingerprint) is True
    decision = await task
    assert decision.granted is True
    hold.set()


@pytest.mark.asyncio
async def test_resolution_bound_to_payload_fingerprint(settings) -> None:
    bus = EventBus()
    manager = _manager(settings, bus)
    first = _payload("req-a", "first question")
    second = _payload("req-b", "second question")
    hold = asyncio.Event()

    async def hanging(proposal, received_payload) -> bool:
        await hold.wait()
        return False

    manager.set_responder(hanging)
    task = asyncio.ensure_future(
        manager.request_consent(
            request_id=first.request_id,
            proposal=_proposal(first),
            payload=first,
        )
    )
    await _wait_pending(manager)

    # A decision for another payload (even if the caller knows a valid
    # fingerprint) must never approve this request.
    assert manager.resolve(first.request_id, True, second.fingerprint) is False
    assert manager.resolve(first.request_id, True, first.fingerprint) is True
    decision = await task
    assert decision.granted is True
    hold.set()


@pytest.mark.asyncio
async def test_no_persistent_consent(settings) -> None:
    bus = EventBus()
    manager = _manager(settings, bus)
    first = _payload("req-1", "first")
    second = _payload("req-2", "second")

    async def approve(proposal, received_payload) -> bool:
        return True

    manager.set_responder(approve)
    assert (
        await manager.request_consent(
            request_id=first.request_id, proposal=_proposal(first), payload=first
        )
    ).granted is True

    # A later request is never approved by the earlier decision.
    manager.clear_responder()
    later = await manager.request_consent(
        request_id=second.request_id, proposal=_proposal(second), payload=second
    )
    assert later.granted is False
    assert later.decided_by == "fail_closed"
    assert manager.get_decision(second.request_id).granted is False


@pytest.mark.asyncio
async def test_empty_request_id_fails_closed(settings) -> None:
    bus = EventBus()
    manager = _manager(settings, bus)
    payload = _payload()
    called = False

    async def approve(proposal, received_payload) -> bool:
        nonlocal called
        called = True
        return True

    manager.set_responder(approve)
    decision = await manager.request_consent(
        request_id="",
        proposal=_proposal(payload),
        payload=payload,
    )
    assert called is False
    assert decision.granted is False
    assert decision.decided_by == "fail_closed"


@pytest.mark.asyncio
async def test_duplicate_request_id_fails_closed(settings) -> None:
    bus = EventBus()
    manager = _manager(settings, bus)
    payload = _payload()
    hold = asyncio.Event()

    async def hanging(proposal, received_payload) -> bool:
        await hold.wait()
        return False

    manager.set_responder(hanging)
    task = asyncio.ensure_future(
        manager.request_consent(
            request_id=payload.request_id,
            proposal=_proposal(payload),
            payload=payload,
        )
    )
    await _wait_pending(manager)

    duplicate = await manager.request_consent(
        request_id=payload.request_id,
        proposal=_proposal(payload),
        payload=payload,
    )
    assert duplicate.granted is False
    assert duplicate.decided_by == "fail_closed"

    manager.resolve(payload.request_id, True, payload.fingerprint)
    assert (await task).granted is True
    hold.set()


@pytest.mark.asyncio
async def test_transport_payload_is_frozen(settings) -> None:
    """Consent preview metadata cannot be mutated after the fact."""
    payload = _payload()
    with pytest.raises(dataclasses.FrozenInstanceError):
        payload.user_message = "tampered"  # type: ignore[misc]
