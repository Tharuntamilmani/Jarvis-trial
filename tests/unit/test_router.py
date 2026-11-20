"""Unified provider router tests (Stage 2.4).

Everything is mocked: fake providers, a real ProviderRegistry/EventBus/
ConsentManager, and one real GroqProvider over ``httpx.MockTransport`` to
verify the actual serialized HTTP body. No test makes a live request.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import time
from collections.abc import AsyncIterator
from types import SimpleNamespace

import httpx
import pytest

import jarvis.intelligence.router as router_module
from jarvis.config import Settings
from jarvis.core.event_bus import EventBus
from jarvis.core.events import (
    CloudConsentDecided,
    CloudFallbackProposed,
    ProviderSelected,
)
from jarvis.core.exceptions import KillSwitchActiveError
from jarvis.intelligence.cloud_payload import (
    PayloadValidationError,
    serialize_cloud_body,
)
from jarvis.intelligence.consent import ConsentManager
from jarvis.intelligence.prompts import JARVIS_SYSTEM_PROMPT
from jarvis.intelligence.providers.base import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    LLMProvider,
    PrivacyLevel,
    ProviderCapabilities,
    ProviderHealth,
)
from jarvis.intelligence.providers.errors import (
    ProviderAuthError,
    ProviderInvalidRequest,
    ProviderRateLimited,
    ProviderResponseError,
    ProviderTimeout,
    ProviderUnavailable,
)
from jarvis.intelligence.providers.groq_provider import GroqProvider
from jarvis.intelligence.providers.registry import ProviderRegistry
from jarvis.intelligence.router import (
    CloudConsentDeniedError,
    LLMRouter,
    NoAvailableProviderError,
)

CURRENT = "current question from the user"
HISTORY = "old conversation turn"
MEMORY = "private memory content"
TOOL_OUTPUT = "private tool output"
FILE_PATH = "C:\\Users\\Tharun\\private-file.txt"


def _settings(tmp_path, **overrides) -> Settings:
    defaults = dict(
        log_dir=tmp_path / "logs",
        routing_max_retries=2,
        routing_budget_seconds=120.0,
        routing_backoff_base_seconds=0.001,
        routing_backoff_max_seconds=0.002,
        health_hard_down_ttl_seconds=5.0,
        _env_file=None,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _request(request_id: str = "req-r1") -> ChatRequest:
    return ChatRequest(
        model=None,
        messages=[
            ChatMessage("system", "hidden local system context"),
            ChatMessage("user", "older question"),
            ChatMessage("assistant", HISTORY),
            ChatMessage("tool", TOOL_OUTPUT),
            ChatMessage("system", MEMORY),
            ChatMessage("user", f"question about {FILE_PATH}"),
            ChatMessage("user", CURRENT),
        ],
        request_id=request_id,
        temperature=0.3,
        max_tokens=64,
    )


class FakeProvider(LLMProvider):
    """Deterministic provider with call counting and scripted outcomes."""

    def __init__(
        self,
        provider_id: str,
        privacy: PrivacyLevel,
        *,
        configured: bool = True,
        outcomes: list[object] | None = None,
        default_model: str = "fake-model",
        health: ProviderHealth | Exception | None = None,
        behavior=None,
    ) -> None:
        self.id = provider_id
        self.capabilities = ProviderCapabilities(
            streaming=True,
            tools=True,
            context_size=100_000,
            privacy=privacy,
        )
        self.configured = configured
        self.outcomes = list(outcomes or [])
        self.calls: list[ChatRequest] = []
        self.health_calls = 0
        self._default_model = default_model
        self._health = health or ProviderHealth(available=True, detail="connected")
        self._behavior = behavior

    @property
    def chat_calls(self) -> int:
        return len(self.calls)

    async def health(self) -> ProviderHealth:
        self.health_calls += 1
        if isinstance(self._health, Exception):
            raise self._health
        return self._health

    async def list_models(self):
        return []

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        if self._behavior is not None:
            return await self._behavior(request)
        if self.outcomes:
            # A single scripted exception models a persistently failing
            # provider; multiple outcomes are consumed in order.
            if len(self.outcomes) == 1 and isinstance(self.outcomes[0], Exception):
                raise self.outcomes[0]
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return ChatResponse(
            content="ok",
            model=request.model or self._default_model,
            provider=self.id,
        )

    def stream(self, request: ChatRequest) -> AsyncIterator:
        async def _empty():
            if False:  # pragma: no cover
                yield None

        return _empty()

    def is_configured(self) -> bool:
        return self.configured


def _harness(
    settings: Settings,
    providers: list[FakeProvider],
    *,
    responder=None,
    kill_switch=None,
    sleep=None,
    clock=None,
    audit=None,
):
    bus = EventBus()
    registry = ProviderRegistry(bus)
    for provider in providers:
        registry.register(provider)
    consent = ConsentManager(settings, bus)
    if responder is not None:
        consent.set_responder(responder)
    router = LLMRouter(
        settings=settings,
        providers=registry,
        bus=bus,
        consent=consent,
        audit=audit,
        kill_switch=kill_switch,
        **({"sleep": sleep} if sleep is not None else {}),
        **({"clock": clock} if clock is not None else {}),
    )
    return router, registry, bus, consent


async def _approve(proposal, payload) -> bool:
    return True


async def _deny(proposal, payload) -> bool:
    return False


def _local(**kwargs) -> FakeProvider:
    return FakeProvider("ollama", PrivacyLevel.LOCAL, **kwargs)


def _cloud(**kwargs) -> FakeProvider:
    return FakeProvider("groq", PrivacyLevel.CLOUD, **kwargs)


# -- Local-first ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_local_success_never_contacts_cloud(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local()
    cloud = _cloud()
    router, _, bus, _ = _harness(
        settings, [local, cloud], responder=_approve
    )
    selected: list[ProviderSelected] = []
    proposals: list[CloudFallbackProposed] = []
    bus.subscribe(ProviderSelected, selected.append)
    bus.subscribe(CloudFallbackProposed, proposals.append)

    outcome = await router.complete(_request())

    assert outcome.provider == "ollama"
    assert outcome.privacy == "local"
    assert outcome.response.content == "ok"
    assert local.chat_calls == 1
    assert cloud.chat_calls == 0
    assert proposals == []
    assert [event.provider for event in selected] == ["ollama"]


@pytest.mark.asyncio
async def test_local_success_keeps_full_history_messages(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local()
    router, _, _, _ = _harness(settings, [local, _cloud()], responder=_approve)
    request = _request()

    await router.complete(request)

    assert local.calls[0].messages == request.messages  # history is allowed locally


@pytest.mark.asyncio
async def test_local_failure_without_groq_credentials_makes_no_cloud_attempt(
    tmp_path,
) -> None:
    settings = _settings(tmp_path)
    local = _local(outcomes=[ProviderUnavailable("Ollama service unreachable")])
    cloud = _cloud(configured=False)
    router, _, bus, consent = _harness(
        settings, [local, cloud], responder=_approve
    )
    proposals: list[CloudFallbackProposed] = []
    bus.subscribe(CloudFallbackProposed, proposals.append)

    with pytest.raises(ProviderUnavailable):
        await router.complete(_request())

    assert local.chat_calls == 3
    assert cloud.chat_calls == 0
    assert proposals == []
    assert consent.pending_count == 0


# -- Consent --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consent_approval_dispatches_only_sanitized_payload(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local(outcomes=[ProviderUnavailable("local down")])
    cloud = _cloud()
    router, _, bus, _ = _harness(settings, [local, cloud], responder=_approve)
    proposals: list[CloudFallbackProposed] = []
    decisions: list[CloudConsentDecided] = []
    selected: list[ProviderSelected] = []
    bus.subscribe(CloudFallbackProposed, proposals.append)
    bus.subscribe(CloudConsentDecided, decisions.append)
    bus.subscribe(ProviderSelected, selected.append)

    outcome = await router.complete(_request())

    assert outcome.provider == "groq"
    assert outcome.cloud_consent == "granted"
    assert local.chat_calls == 3
    assert cloud.chat_calls == 1
    assert len(proposals) == 1
    assert proposals[0].request_id == "req-r1"
    assert proposals[0].payload_fingerprint
    assert decisions[0].granted is True
    assert [event.provider for event in selected] == ["ollama", "groq"]

    sent = cloud.calls[0]
    assert len(sent.messages) == 2
    assert [m.role for m in sent.messages] == ["system", "user"]
    assert sent.messages[0].content == JARVIS_SYSTEM_PROMPT
    assert sent.messages[1].content == CURRENT
    serialized = json.dumps(
        [{"role": m.role, "content": m.content} for m in sent.messages]
    )
    for forbidden in (HISTORY, MEMORY, TOOL_OUTPUT, FILE_PATH, "older question"):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_explicit_groq_still_requires_consent(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local()
    cloud = _cloud()
    router, _, bus, _ = _harness(settings, [local, cloud])  # no responder
    proposals: list[CloudFallbackProposed] = []
    bus.subscribe(CloudFallbackProposed, proposals.append)

    with pytest.raises(CloudConsentDeniedError):
        await router.complete(_request(), provider="groq")

    assert local.chat_calls == 0
    assert cloud.chat_calls == 0
    assert len(proposals) == 1
    assert proposals[0].provider == "groq"
    assert proposals[0].payload_fingerprint


@pytest.mark.asyncio
async def test_consent_rejection_fails_closed_without_http(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local(outcomes=[ProviderUnavailable("local down")])
    cloud = _cloud()
    router, _, bus, _ = _harness(settings, [local, cloud], responder=_deny)
    decisions: list[CloudConsentDecided] = []
    bus.subscribe(CloudConsentDecided, decisions.append)

    with pytest.raises(CloudConsentDeniedError):
        await router.complete(_request())

    assert cloud.chat_calls == 0
    assert decisions and decisions[0].granted is False
    assert decisions[0].decided_by == "user"


@pytest.mark.asyncio
async def test_consent_timeout_fails_closed_without_http(tmp_path) -> None:
    settings = _settings(tmp_path, cloud_consent_timeout_seconds=0.05)
    local = _local(outcomes=[ProviderUnavailable("local down")])
    cloud = _cloud()

    async def slow(proposal, payload) -> bool:
        await asyncio.sleep(5)
        return True

    router, _, bus, _ = _harness(settings, [local, cloud], responder=slow)
    decisions: list[CloudConsentDecided] = []
    bus.subscribe(CloudConsentDecided, decisions.append)

    with pytest.raises(CloudConsentDeniedError):
        await router.complete(_request())

    assert cloud.chat_calls == 0
    assert decisions[0].decided_by == "timeout"


@pytest.mark.asyncio
async def test_no_responder_defaults_to_denied(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local(outcomes=[ProviderUnavailable("local down")])
    cloud = _cloud()
    router, _, _, _ = _harness(settings, [local, cloud])  # no responder

    with pytest.raises(CloudConsentDeniedError):
        await router.complete(_request())

    assert cloud.chat_calls == 0


@pytest.mark.asyncio
async def test_changed_payload_after_consent_blocks_dispatch(
    tmp_path, monkeypatch
) -> None:
    settings = _settings(tmp_path)
    local = _local(outcomes=[ProviderUnavailable("local down")])
    cloud = _cloud()
    real_build = router_module.build_cloud_payload

    def tampering_build(request, *, system_prompt, model_override=None):
        payload = real_build(
            request, system_prompt=system_prompt, model_override=model_override
        )
        return dataclasses.replace(payload, user_message="exfiltrate secrets")

    monkeypatch.setattr(router_module, "build_cloud_payload", tampering_build)
    router, _, _, _ = _harness(settings, [local, cloud], responder=_approve)

    with pytest.raises(PayloadValidationError):
        await router.complete(_request())

    assert cloud.chat_calls == 0


# -- Error classification and retries ------------------------------------------


@pytest.mark.asyncio
async def test_auth_error_is_not_retried_and_never_falls_back(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local(outcomes=[ProviderAuthError("bad key")])
    cloud = _cloud()
    router, _, _, _ = _harness(settings, [local, cloud], responder=_approve)

    with pytest.raises(ProviderAuthError):
        await router.complete(_request())

    assert local.chat_calls == 1
    assert cloud.chat_calls == 0


@pytest.mark.asyncio
async def test_invalid_request_is_not_retried_and_never_falls_back(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local(outcomes=[ProviderInvalidRequest("bad model")])
    cloud = _cloud()
    router, _, _, _ = _harness(settings, [local, cloud], responder=_approve)

    with pytest.raises(ProviderInvalidRequest):
        await router.complete(_request())

    assert local.chat_calls == 1
    assert cloud.chat_calls == 0


@pytest.mark.asyncio
async def test_response_error_is_not_retried_but_may_fall_back(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local(outcomes=[ProviderResponseError("bad response")])
    cloud = _cloud()
    router, _, _, _ = _harness(settings, [local, cloud], responder=_approve)

    outcome = await router.complete(_request())

    assert outcome.provider == "groq"
    assert local.chat_calls == 1  # non-transient: no retry
    assert cloud.chat_calls == 1


@pytest.mark.asyncio
async def test_rate_limit_respects_retry_after(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local(
        outcomes=[
            ProviderRateLimited("busy", retry_after=1.25),
            ProviderRateLimited("busy", retry_after=1.25),
            ChatResponse(content="ok", model="fake-model", provider="ollama"),
        ]
    )
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    router, _, _, _ = _harness(
        settings, [local, _cloud()], responder=_approve, sleep=fake_sleep
    )

    outcome = await router.complete(_request(), provider="ollama")

    assert outcome.provider == "ollama"
    assert outcome.retries == 2
    assert delays == [1.25, 1.25]
    assert local.chat_calls == 3


@pytest.mark.asyncio
async def test_retry_limit_is_two_retries_per_provider(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local(
        outcomes=[
            ProviderUnavailable("down"),
            ProviderUnavailable("down"),
            ProviderUnavailable("down"),
            ProviderUnavailable("should-not-be-used"),
        ]
    )
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    router, _, _, _ = _harness(
        settings, [local, _cloud()], responder=_approve, sleep=fake_sleep
    )

    with pytest.raises(ProviderUnavailable):
        await router.complete(_request(), provider="ollama")

    assert local.chat_calls == 3  # first attempt + 2 retries
    assert len(delays) == 2


@pytest.mark.asyncio
async def test_exponential_backoff_is_bounded(tmp_path) -> None:
    settings = _settings(
        tmp_path, routing_backoff_base_seconds=1.0, routing_backoff_max_seconds=1.5
    )
    local = _local(
        outcomes=[
            ProviderUnavailable("a"),
            ProviderUnavailable("b"),
            ChatResponse(content="ok", model="fake-model", provider="ollama"),
        ]
    )
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    router, _, _, _ = _harness(
        settings, [local, _cloud()], responder=_approve, sleep=fake_sleep
    )

    await router.complete(_request(), provider="ollama")

    assert delays == [1.0, 1.5]  # 1 * 2^1 capped at 1.5


@pytest.mark.asyncio
async def test_total_budget_bounds_the_request(tmp_path) -> None:
    settings = _settings(
        tmp_path,
        routing_budget_seconds=0.05,
        routing_backoff_base_seconds=1.0,
        routing_backoff_max_seconds=1.0,
    )
    local = _local(behavior=lambda request: asyncio.sleep(5))
    router, _, _, _ = _harness(settings, [local, _cloud()], responder=_approve)

    with pytest.raises(ProviderTimeout):
        await router.complete(_request(), provider="ollama")

    assert local.chat_calls == 1  # timed out; remaining budget forbids a retry


# -- Health cache ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_fresh_hard_down_skips_local_attempt(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local(health=ProviderHealth(False, "Ollama service unreachable", hard_down=True))
    cloud = _cloud()
    router, registry, _, _ = _harness(
        settings, [local, cloud], responder=_approve
    )
    await registry.refresh_health("ollama")

    outcome = await router.complete(_request())

    assert outcome.provider == "groq"
    assert local.chat_calls == 0
    assert cloud.chat_calls == 1


@pytest.mark.asyncio
async def test_stale_hard_down_never_suppresses_real_attempt(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local(health=ProviderHealth(False, "Ollama service unreachable", hard_down=True))
    cloud = _cloud()
    router, registry, _, _ = _harness(settings, [local, cloud], responder=_approve)
    await registry.refresh_health("ollama")
    registry._health_at["ollama"] = time.monotonic() - 30  # stale

    outcome = await router.complete(_request())

    assert outcome.provider == "ollama"
    assert local.chat_calls == 1
    assert cloud.chat_calls == 0


@pytest.mark.asyncio
async def test_cached_unavailable_without_hard_down_still_attempts_local(
    tmp_path,
) -> None:
    settings = _settings(tmp_path)
    local = _local()
    cloud = _cloud()
    router, registry, _, _ = _harness(settings, [local, cloud], responder=_approve)
    registry._health["ollama"] = ProviderHealth(False, "degraded", hard_down=False)
    registry._health_at["ollama"] = time.monotonic()

    outcome = await router.complete(_request())

    assert outcome.provider == "ollama"
    assert local.chat_calls == 1


@pytest.mark.asyncio
async def test_actual_failure_triggers_health_refresh(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local(outcomes=[ProviderUnavailable("local down")])
    cloud = _cloud()
    router, _, _, _ = _harness(settings, [local, cloud], responder=_approve)

    await router.complete(_request())

    assert local.health_calls >= 1


@pytest.mark.asyncio
async def test_health_refresh_failure_does_not_crash_routing(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local(
        outcomes=[ProviderUnavailable("local down")],
        health=RuntimeError("health probe exploded"),
    )
    cloud = _cloud()
    router, _, _, _ = _harness(settings, [local, cloud], responder=_approve)

    outcome = await router.complete(_request())

    assert outcome.provider == "groq"
    assert cloud.chat_calls == 1


# -- Cancellation and kill switch ----------------------------------------------


@pytest.mark.asyncio
async def test_cancellation_never_falls_back(tmp_path) -> None:
    settings = _settings(tmp_path)
    started = asyncio.Event()

    async def forever(request):
        started.set()
        await asyncio.sleep(3600)

    local = _local(behavior=forever)
    cloud = _cloud()
    router, _, bus, _ = _harness(settings, [local, cloud], responder=_approve)
    proposals: list[CloudFallbackProposed] = []
    bus.subscribe(CloudFallbackProposed, proposals.append)

    task = asyncio.ensure_future(router.complete(_request()))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert cloud.chat_calls == 0
    assert proposals == []


@pytest.mark.asyncio
async def test_kill_switch_blocks_all_network_requests(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local()
    cloud = _cloud()
    router, _, bus, _ = _harness(
        settings,
        [local, cloud],
        responder=_approve,
        kill_switch=SimpleNamespace(active=True),
    )
    proposals: list[CloudFallbackProposed] = []
    bus.subscribe(CloudFallbackProposed, proposals.append)

    with pytest.raises(KillSwitchActiveError):
        await router.complete(_request())

    assert local.chat_calls == 0
    assert cloud.chat_calls == 0
    assert proposals == []


@pytest.mark.asyncio
async def test_kill_switch_engaged_during_consent_blocks_dispatch(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local(outcomes=[ProviderUnavailable("local down")])
    cloud = _cloud()
    switch = SimpleNamespace(active=False)

    async def engage_then_approve(proposal, payload) -> bool:
        switch.active = True
        return True

    router, _, _, _ = _harness(
        settings, [local, cloud], responder=engage_then_approve, kill_switch=switch
    )

    with pytest.raises(KillSwitchActiveError):
        await router.complete(_request())

    assert cloud.chat_calls == 0


# -- Explicit routing / no switching -------------------------------------------


@pytest.mark.asyncio
async def test_explicit_local_failure_does_not_switch_to_cloud(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local(outcomes=[ProviderUnavailable("down")] * 3)
    cloud = _cloud()
    router, _, _, _ = _harness(settings, [local, cloud], responder=_approve)

    with pytest.raises(ProviderUnavailable):
        await router.complete(_request(), provider="ollama")

    assert local.chat_calls == 3
    assert cloud.chat_calls == 0


@pytest.mark.asyncio
async def test_groq_failure_does_not_switch_back_to_local(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local(outcomes=[ProviderUnavailable("local down")])
    cloud = _cloud(outcomes=[ProviderUnavailable("groq down")] * 3)
    router, _, _, _ = _harness(settings, [local, cloud], responder=_approve)

    with pytest.raises(ProviderUnavailable):
        await router.complete(_request())

    assert local.chat_calls == 3  # no extra local attempt after Groq failed
    assert cloud.chat_calls == 3


@pytest.mark.asyncio
async def test_explicit_groq_auth_error_not_retried(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local()
    cloud = _cloud(outcomes=[ProviderAuthError("rejected")])
    router, _, _, _ = _harness(settings, [local, cloud], responder=_approve)

    with pytest.raises(ProviderAuthError):
        await router.complete(_request(), provider="groq")

    assert local.chat_calls == 0
    assert cloud.chat_calls == 1


@pytest.mark.asyncio
async def test_explicit_model_override_reaches_provider(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local()
    cloud = _cloud()
    router, _, bus, _ = _harness(settings, [local, cloud], responder=_approve)
    selected: list[ProviderSelected] = []
    bus.subscribe(ProviderSelected, selected.append)

    await router.complete(
        _request(), provider="groq", model="llama-3.1-8b-instant"
    )

    assert cloud.calls[0].model == "llama-3.1-8b-instant"
    assert selected[0].model == "llama-3.1-8b-instant"


@pytest.mark.asyncio
async def test_unknown_provider_override_rejected(tmp_path) -> None:
    settings = _settings(tmp_path)
    router, _, _, _ = _harness(settings, [_local(), _cloud()], responder=_approve)

    with pytest.raises(router_module.RoutingError):
        await router.complete(_request(), provider="nope")


@pytest.mark.asyncio
async def test_no_available_provider(tmp_path) -> None:
    settings = _settings(tmp_path)
    router, _, _, _ = _harness(settings, [], responder=_approve)
    with pytest.raises(NoAvailableProviderError):
        await router.complete(_request())


# -- Actual serialized HTTP body (real GroqProvider, mocked transport) ----------


def _groq_with_transport(settings, captured: list[dict]) -> GroqProvider:
    async def fake_handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "model": settings.groq_model,
                "choices": [
                    {
                        "message": {"content": "cloud answer"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"total_tokens": 12},
            },
        )

    provider = GroqProvider(
        settings, transport=httpx.MockTransport(fake_handler)
    )
    provider.is_configured = lambda: True  # bypass DPAPI for this transport test
    provider._auth_headers = lambda: {"Authorization": "Bearer test-key"}
    return provider


@pytest.mark.asyncio
async def test_actual_serialized_cloud_http_body_is_isolated(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local(outcomes=[ProviderUnavailable("local down")])
    captured: list[dict] = []
    groq = _groq_with_transport(settings, captured)
    router, _, _, _ = _harness(settings, [local, groq], responder=_approve)

    try:
        outcome = await router.complete(_request())
    finally:
        await groq.aclose()

    assert outcome.provider == "groq"
    assert len(captured) == 1
    body = captured[0]
    assert body == {
        "model": settings.groq_model,
        "messages": [
            {"role": "system", "content": JARVIS_SYSTEM_PROMPT},
            {"role": "user", "content": CURRENT},
        ],
        "temperature": 0.3,
        "stream": False,
        "max_tokens": 64,
    }
    # The validated serialized form is byte-for-byte the same scope.
    assert body == serialize_cloud_body(
        # reconstruct the same consented payload scope
        router_module.build_cloud_payload(
            _request(),
            system_prompt=JARVIS_SYSTEM_PROMPT,
        ),
        default_model=settings.groq_model,
    )
    serialized = json.dumps(body)
    for forbidden in (HISTORY, MEMORY, TOOL_OUTPUT, FILE_PATH, "older question"):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_zero_unauthorized_groq_http_requests(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local(outcomes=[ProviderUnavailable("local down")])
    captured: list[dict] = []
    groq = _groq_with_transport(settings, captured)
    router, _, _, _ = _harness(settings, [local, groq])  # no consent responder

    try:
        with pytest.raises(CloudConsentDeniedError):
            await router.complete(_request())
    finally:
        await groq.aclose()

    assert captured == []  # zero chat HTTP requests


@pytest.mark.asyncio
async def test_cloud_retry_revalidates_every_serialized_body(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local(outcomes=[ProviderUnavailable("local down")])
    bodies: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content.decode("utf-8")))
        if len(bodies) == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(
            200,
            json={
                "model": settings.groq_model,
                "choices": [
                    {"message": {"content": "ok"}, "finish_reason": "stop"}
                ],
                "usage": {"total_tokens": 3},
            },
        )

    groq = GroqProvider(settings, transport=httpx.MockTransport(handler))
    groq.is_configured = lambda: True
    groq._auth_headers = lambda: {"Authorization": "Bearer test-key"}

    async def healthy() -> ProviderHealth:
        return ProviderHealth(available=True, detail="connected")

    groq.health = healthy  # avoid an extra /models request during refresh

    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    router, _, _, _ = _harness(
        settings, [local, groq], responder=_approve, sleep=fake_sleep
    )

    try:
        outcome = await router.complete(_request())
    finally:
        await groq.aclose()

    assert outcome.provider == "groq"
    assert outcome.retries == 1
    assert delays[-1] == 1.0  # Retry-After honored on the cloud attempt
    assert len(bodies) == 2  # both attempts validated and transmitted
    for body in bodies:
        assert [m["role"] for m in body["messages"]] == ["system", "user"]
        assert body["messages"][1]["content"] == CURRENT
        serialized = json.dumps(body)
        for forbidden in (HISTORY, MEMORY, TOOL_OUTPUT, FILE_PATH, "older question"):
            assert forbidden not in serialized


# -- Capability validation / engine registration --------------------------------


def test_stage_2_4_routing_defaults(tmp_path) -> None:
    settings = Settings(log_dir=tmp_path / "logs", _env_file=None)
    assert settings.routing_max_retries == 2
    assert settings.routing_budget_seconds == 120.0
    assert settings.routing_backoff_max_seconds > 0
    assert settings.health_hard_down_ttl_seconds == 5.0
    assert settings.cloud_consent_timeout_seconds > 0


@pytest.mark.asyncio
async def test_context_size_capability_is_enforced(tmp_path) -> None:
    settings = _settings(tmp_path)
    local = _local()
    local.capabilities = ProviderCapabilities(
        streaming=True, tools=True, context_size=1, privacy=PrivacyLevel.LOCAL
    )
    router, _, _, _ = _harness(settings, [local, _cloud()], responder=_approve)

    with pytest.raises(ProviderInvalidRequest, match="context size"):
        await router.complete(_request(), provider="ollama")

    assert local.chat_calls == 0


@pytest.mark.asyncio
async def test_privacy_mismatch_is_rejected_before_dispatch(tmp_path) -> None:
    settings = _settings(tmp_path)
    # A provider registered as "ollama" but labelled cloud must never be
    # used by the local route.
    mislabelled = FakeProvider("ollama", PrivacyLevel.CLOUD)
    router, _, _, _ = _harness(settings, [mislabelled, _cloud()], responder=_approve)

    with pytest.raises(router_module.RoutingError, match="privacy"):
        await router.complete(_request())

    assert mislabelled.chat_calls == 0


@pytest.mark.asyncio
async def test_engine_registers_router_consent_and_chat(tmp_path) -> None:
    from jarvis.core.engine import CoreEngine
    from jarvis.intelligence.chat_service import ChatService

    settings = _settings(tmp_path)
    engine = CoreEngine(settings)
    try:
        engine.assemble()
        assert {"consent", "router", "chat"} <= set(engine.registry.names)
        router = await engine.registry.get("router")
        assert isinstance(router, LLMRouter)
        chat = await engine.registry.get("chat")
        assert isinstance(chat, ChatService)
    finally:
        chat = engine.registry.try_get("chat")
        if chat is not None:
            await chat.close()
        providers = engine.registry.try_get("providers")
        if providers is not None:
            await providers.close()
