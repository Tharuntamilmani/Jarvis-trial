"""Unified provider router (Stage 2.4).

Routing policy
--------------
- Local-first: Ollama is the default provider; a local attempt is made
  before any cloud fallback.
- Explicit overrides (``provider=`` / ``model=``) bypass automatic
  fallback in both directions.
- Cloud dispatch is possible ONLY after an explicit, per-request consent
  decision that is bound to the sanitized payload fingerprint.
- Cloud payload isolation is enforced here, immediately before dispatch:
  history, memories, files, tool output, and local state never leave the
  machine (see :mod:`jarvis.intelligence.cloud_payload`).

Retry policy
------------
- At most ``routing_max_retries`` retries per provider per request.
- Only transient failures (timeout, unavailable, rate limited) retry.
- ``Retry-After`` is honored for 429 responses.
- Exponential backoff is bounded by ``routing_backoff_max_seconds``.
- One total request budget (default 120 s) spans retries and consent.
- Cancellation, authentication failures, invalid requests, and an active
  kill switch never retry and never fall back.

Health cache
------------
Cached health is primarily for UI display. A *fresh* hard connection
refusal may skip one local attempt for at most
``health_hard_down_ttl_seconds``; stale health never suppresses a real
attempt. Actual failures trigger a best-effort health refresh and a
refresh failure never crashes routing.

This module is UI-agnostic: no PySide6, no HTTP server, and no ChatService.
Streaming orchestration (public tokens, cancellation, assistant state)
belongs to :mod:`jarvis.intelligence.chat_service` (Stage 2.5); this module
only provides consent-gated, local-first streaming routing.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from jarvis.config import Settings
from jarvis.core.event_bus import EventBus
from jarvis.core.events import CloudFallbackProposed, ProviderSelected
from jarvis.core.exceptions import JarvisError, KillSwitchActiveError
from jarvis.core.lifecycle import LifecycleManager
from jarvis.intelligence.cloud_payload import (
    PayloadValidationError,
    build_cloud_payload,
    serialize_cloud_body,
    validate_cloud_dispatch,
    validate_serialized_cloud_body,
)
from jarvis.intelligence.consent import ConsentManager
from jarvis.intelligence.prompts import JARVIS_SYSTEM_PROMPT
from jarvis.intelligence.providers.base import (
    ChatRequest,
    ChatResponse,
    LLMProvider,
    PrivacyLevel,
    StreamCancelled,
    StreamDone,
    StreamError,
    StreamStart,
    Token,
)
from jarvis.intelligence.providers.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderInvalidRequest,
    ProviderRateLimited,
    ProviderResponseError,
    ProviderTimeout,
    ProviderUnavailable,
)
from jarvis.intelligence.providers.registry import ProviderRegistry
from jarvis.monitoring.logging import get_logger
from jarvis.security.audit import AuditService
from jarvis.security.kill_switch import KillSwitchService

log = get_logger(__name__)

_LOCAL_ALIASES = {"local": "ollama"}
_CLOUD_ALIASES = {"cloud": "groq"}
_RETRYABLE_ERRORS = (ProviderTimeout, ProviderUnavailable, ProviderRateLimited)
_ALLOWED_ROLES = frozenset({"system", "user", "assistant", "tool"})


class RoutingError(JarvisError):
    """The router could not satisfy a request."""


class NoAvailableProviderError(RoutingError):
    """No registered provider can serve the request."""


class CloudConsentDeniedError(RoutingError):
    """Cloud dispatch was not authorized by an explicit consent decision."""


@dataclass(frozen=True)
class RouteOutcome:
    """Result of one routed request."""

    response: ChatResponse
    provider: str
    model: str
    privacy: str
    request_id: str
    retries: int = 0
    cloud_consent: str = ""  # "" | granted | denied | not_required


@dataclass(frozen=True)
class StreamRouted:
    """Metadata announcing the provider finally selected for a stream.

    Yielded exactly once, immediately before the selected provider's
    ``StreamStart``. It contains no prompts, context, credentials or
    machine details — only routing metadata safe for every consumer.
    """

    request_id: str
    provider: str
    model: str
    privacy: str  # local | cloud
    cloud_consent: str = ""  # "" | granted | denied | not_required


@dataclass(frozen=True)
class _StreamDispatch:
    """One provider dispatch plan (already validated for cloud consent)."""

    provider_id: str
    request: ChatRequest
    expected_privacy: PrivacyLevel
    cloud_consent: str = ""
    dispatch_guard: Callable[[ChatRequest], None] | None = None


class LLMRouter:
    """Local-first, consent-gated unified provider router."""

    def __init__(
        self,
        *,
        settings: Settings,
        providers: ProviderRegistry,
        bus: EventBus,
        consent: ConsentManager,
        audit: AuditService | None = None,
        lifecycle: LifecycleManager | None = None,
        kill_switch: KillSwitchService | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._settings = settings
        self._providers = providers
        self._bus = bus
        self._consent = consent
        self._audit_service = audit
        self._lifecycle = lifecycle
        self._kill_switch = kill_switch
        self._clock = clock
        self._sleep = sleep

        # Bound once so tests can tune them without mutating frozen Settings.
        self._max_retries = int(settings.routing_max_retries)
        self._budget = float(settings.routing_budget_seconds)
        self._backoff_base = float(settings.routing_backoff_base_seconds)
        self._backoff_max = float(
            getattr(settings, "routing_backoff_max_seconds", 8.0)
        )
        self._hard_down_ttl = float(settings.health_hard_down_ttl_seconds)

    # -- Public API -------------------------------------------------------------

    async def complete(
        self,
        request: ChatRequest,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> RouteOutcome:
        """Route one non-streaming chat request.

        ``provider`` / ``model`` are explicit overrides. Without them the
        router attempts Ollama first and may propose a consent-gated cloud
        fallback. Cancellation propagates unchanged and never falls back.
        """
        if not request.request_id:
            raise ProviderInvalidRequest("chat request requires a request_id")
        self._validate_messages(request)
        self._ensure_network_allowed()

        deadline = self._clock() + self._budget
        explicit = self._normalize_provider(provider)
        if explicit is not None:
            return await self._route_explicit(
                explicit, request, model=model, deadline=deadline
            )
        return await self._route_local_first(request, model=model, deadline=deadline)

    async def stream_chat(
        self,
        request: ChatRequest,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> AsyncIterator[
        StreamRouted | StreamStart | Token | StreamDone | StreamError | StreamCancelled
    ]:
        """Route one streaming chat request.

        Yields :class:`StreamRouted` exactly once (actual provider, model,
        privacy, consent status), immediately before that provider's
        ``StreamStart``, followed by the provider's own stream events.

        Commitment rule (stricter than "tokens emitted"): automatic
        local→cloud fallback is possible only while the local attempt has
        not produced a ``StreamStart``. Once ``StreamStart`` is yielded the
        provider is committed for the request; any later failure is
        forwarded as ``StreamError`` and never switches providers.

        Pre-stream routing failures raise (consent denial, auth, invalid
        request, kill switch, no provider). Cancellation propagates as
        ``asyncio.CancelledError`` and closes the provider stream.
        """
        self.validate_request(request)
        self._ensure_network_allowed()

        deadline = self._clock() + self._budget
        explicit = self._normalize_provider(provider)
        if explicit is not None:
            selected = self._providers.get(explicit)
            if self._privacy(selected) is PrivacyLevel.CLOUD:
                dispatch = await self._prepare_cloud_dispatch(
                    request,
                    model_override=model,
                    reason="explicit cloud provider selection",
                    deadline=deadline,
                    cloud_consent="granted",
                    provider_id=explicit,
                )
            else:
                dispatch = _StreamDispatch(
                    provider_id=explicit,
                    request=self._with_model(request, model),
                    expected_privacy=PrivacyLevel.LOCAL,
                )
            routed = False
            async for event in self._stream_dispatch(dispatch, deadline=deadline):
                if not routed:
                    routed = True
                    yield self._stream_routed(dispatch, event)
                yield event
            return

        async for event in self._stream_local_first(
            request, model=model, deadline=deadline
        ):
            yield event

    def validate_request(self, request: ChatRequest) -> None:
        """Validate a request before any dispatch (shared with ChatService)."""
        if not request.request_id:
            raise ProviderInvalidRequest("chat request requires a request_id")
        self._validate_messages(request)

    # -- Provider selection -------------------------------------------------------

    def _normalize_provider(self, provider: str | None) -> str | None:
        if provider is None:
            return None
        name = provider.strip().lower()
        if not name or name == "auto":
            return None
        name = _LOCAL_ALIASES.get(name, _CLOUD_ALIASES.get(name, name))
        if name not in self._providers.ids:
            raise RoutingError(f"unknown provider override: {provider!r}")
        return name

    def _local_provider(self) -> tuple[str, LLMProvider] | None:
        if "ollama" in self._providers.ids:
            return "ollama", self._providers.get("ollama")
        for provider_id in self._providers.ids:
            candidate = self._providers.get(provider_id)
            if self._privacy(candidate) is PrivacyLevel.LOCAL:
                return provider_id, candidate
        return None

    def _cloud_provider(self) -> tuple[str, LLMProvider] | None:
        if "groq" not in self._providers.ids:
            return None
        return "groq", self._providers.get("groq")

    async def _route_explicit(
        self,
        provider_id: str,
        request: ChatRequest,
        *,
        model: str | None,
        deadline: float,
    ) -> RouteOutcome:
        provider = self._providers.get(provider_id)
        privacy = self._privacy(provider)
        if privacy is PrivacyLevel.CLOUD:
            return await self._route_cloud(
                request,
                model_override=model,
                reason="explicit cloud provider selection",
                deadline=deadline,
                cloud_consent="granted",
                provider_id=provider_id,
            )
        return await self._attempt(
            provider_id,
            self._with_model(request, model),
            deadline=deadline,
            expected_privacy=PrivacyLevel.LOCAL,
        )

    async def _route_local_first(
        self,
        request: ChatRequest,
        *,
        model: str | None,
        deadline: float,
    ) -> RouteOutcome:
        local = self._local_provider()
        local_request = self._with_model(request, model)

        if local is not None:
            provider_id, provider = local
            if not self._is_fresh_hard_down(provider_id):
                try:
                    return await self._attempt(
                        provider_id,
                        local_request,
                        deadline=deadline,
                        expected_privacy=PrivacyLevel.LOCAL,
                    )
                except asyncio.CancelledError:
                    raise  # cancellation never falls back
                except (ProviderAuthError, ProviderInvalidRequest):
                    raise  # never fall back after auth / invalid request
                except KillSwitchActiveError:
                    raise  # never fall back while the kill switch is active
                except ProviderError as local_error:
                    return await self._fallback(
                        request,
                        reason=str(local_error),
                        model=model,
                        deadline=deadline,
                    )
            reason = "local provider recently refused connections"
        else:
            reason = "no local provider is registered"

        return await self._fallback(
            request, reason=reason, model=model, deadline=deadline
        )

    async def _stream_local_first(
        self,
        request: ChatRequest,
        *,
        model: str | None,
        deadline: float,
    ) -> AsyncIterator[
        StreamRouted | StreamStart | Token | StreamDone | StreamError | StreamCancelled
    ]:
        """Automatic streaming route: local first, consent-gated fallback.

        Fallback is allowed only while the local attempt has not produced a
        ``StreamStart``. Once forwarded, the provider is committed.
        """
        local = self._local_provider()

        if local is not None and not self._is_fresh_hard_down(local[0]):
            provider_id, _ = local
            dispatch = _StreamDispatch(
                provider_id=provider_id,
                request=self._with_model(request, model),
                expected_privacy=PrivacyLevel.LOCAL,
            )
            routed = False
            try:
                async for event in self._stream_dispatch(
                    dispatch, deadline=deadline
                ):
                    if not routed:
                        if not isinstance(event, StreamStart):
                            raise ProviderResponseError(
                                f"{provider_id} stream did not start with StreamStart"
                            )
                        routed = True
                        yield self._stream_routed(dispatch, event)
                    yield event
                return
            except asyncio.CancelledError:
                raise  # cancellation never falls back
            except (ProviderAuthError, ProviderInvalidRequest):
                raise  # never fall back after auth / invalid request
            except KillSwitchActiveError:
                raise  # never fall back while the kill switch is active
            except ProviderError as local_error:
                if routed:
                    # StreamStart was already forwarded: the provider is
                    # committed for this request. Never switch providers.
                    raise
                reason = str(local_error)
        elif local is not None:
            reason = "local provider recently refused connections"
        else:
            reason = "no local provider is registered"

        cloud = self._cloud_provider()
        if cloud is None:
            raise NoAvailableProviderError(
                "local provider unavailable and no cloud provider is registered"
            )
        provider_id, provider = cloud
        if not provider.is_configured():
            raise ProviderUnavailable(
                "cloud fallback unavailable: Groq is not configured"
            )
        dispatch = await self._prepare_cloud_dispatch(
            request,
            model_override=model,
            reason=reason,
            deadline=deadline,
            cloud_consent="granted",
            provider_id=provider_id,
            stream=True,
        )
        routed = False
        async for event in self._stream_dispatch(dispatch, deadline=deadline):
            if not routed:
                routed = True
                yield self._stream_routed(dispatch, event)
            yield event

    def _stream_routed(
        self, dispatch: _StreamDispatch, event: StreamStart
    ) -> StreamRouted:
        """Build safe routing metadata for the resolved provider stream."""
        return StreamRouted(
            request_id=dispatch.request.request_id,
            provider=dispatch.provider_id,
            model=event.model,
            privacy=str(dispatch.expected_privacy.value),
            cloud_consent=dispatch.cloud_consent,
        )

    async def _stream_dispatch(
        self,
        dispatch: _StreamDispatch,
        *,
        deadline: float,
    ) -> AsyncIterator[
        StreamStart | Token | StreamDone | StreamError | StreamCancelled
    ]:
        """Dispatch one provider stream with bounded pre-start retries.

        The first yielded event is always ``StreamStart``. Pre-start
        failures raise after retries are exhausted (so the caller may fall
        back); once ``StreamStart`` is yielded no retry or fallback occurs
        and all subsequent events are forwarded unchanged.
        """
        provider = self._providers.get(dispatch.provider_id)
        self._validate_provider(
            provider, dispatch.request, expected_privacy=dispatch.expected_privacy
        )

        announced = False
        retries = 0
        while True:
            self._ensure_network_allowed()
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise ProviderTimeout("routing request budget exhausted")

            if not announced:
                await self._publish_selected(provider, dispatch.request)
                announced = True
            if dispatch.dispatch_guard is not None:
                dispatch.dispatch_guard(dispatch.request)

            attempt_timeout = remaining
            if dispatch.request.timeout is not None:
                attempt_timeout = min(attempt_timeout, dispatch.request.timeout)

            generator = provider.stream(dispatch.request)
            saw_start = False
            error: ProviderError | None = None
            try:
                while True:
                    try:
                        if saw_start:
                            event = await generator.__anext__()
                        else:
                            event = await asyncio.wait_for(
                                generator.__anext__(), timeout=attempt_timeout
                            )
                    except StopAsyncIteration:
                        if saw_start:
                            yield StreamError(
                                request_id=dispatch.request.request_id,
                                error=ProviderResponseError(
                                    f"{dispatch.provider_id} stream ended "
                                    "without a terminal event"
                                ),
                            )
                            return
                        error = ProviderResponseError(
                            f"{dispatch.provider_id} stream ended before starting"
                        )
                        break
                    except asyncio.TimeoutError:
                        error = ProviderTimeout(
                            f"{dispatch.provider_id} request timed out"
                        )
                        break
                    except asyncio.CancelledError:
                        raise  # never retried, never followed by a fallback
                    except ProviderError as exc:
                        error = exc
                        break
                    except Exception as exc:  # noqa: BLE001
                        error = ProviderUnavailable(
                            f"{dispatch.provider_id} request failed "
                            f"({type(exc).__name__})"
                        )
                        break

                    if not isinstance(
                        event,
                        (StreamStart, Token, StreamDone, StreamError, StreamCancelled),
                    ):
                        violation = ProviderResponseError(
                            f"{dispatch.provider_id} emitted an unsupported "
                            "stream event"
                        )
                        if saw_start:
                            yield StreamError(
                                request_id=dispatch.request.request_id,
                                error=violation,
                            )
                            return
                        error = violation
                        break

                    if event.request_id != dispatch.request.request_id:
                        violation = ProviderResponseError(
                            f"{dispatch.provider_id} stream request ID mismatch"
                        )
                        if saw_start:
                            yield StreamError(
                                request_id=dispatch.request.request_id,
                                error=violation,
                            )
                            return
                        error = violation
                        break

                    if not saw_start:
                        if isinstance(event, StreamStart):
                            saw_start = True
                            yield event
                            continue
                        if isinstance(event, StreamError):
                            error = event.error
                            break
                        error = ProviderResponseError(
                            f"{dispatch.provider_id} emitted a "
                            f"{type(event).__name__} before StreamStart"
                        )
                        break

                    yield event
                    if isinstance(event, (StreamDone, StreamError, StreamCancelled)):
                        return
            finally:
                await generator.aclose()

            # Only pre-StreamStart failures may be retried or fall back.
            # Once StreamStart was forwarded, the provider is committed for
            # this request: surface the failure immediately.
            if saw_start:
                failure = error or ProviderResponseError(
                    f"{dispatch.provider_id} stream failed after starting"
                )
                await self._audit_failure(
                    dispatch.provider_id,
                    dispatch.request.request_id,
                    failure,
                    retries,
                )
                raise failure

            assert error is not None
            await self._refresh_health_safely(dispatch.provider_id)
            if not isinstance(error, _RETRYABLE_ERRORS) or retries >= self._max_retries:
                await self._audit_failure(
                    dispatch.provider_id, dispatch.request.request_id, error, retries
                )
                raise error
            delay = self._retry_delay(error, retries)
            if self._clock() + delay >= deadline:
                await self._audit_failure(
                    dispatch.provider_id, dispatch.request.request_id, error, retries
                )
                raise error
            retries += 1
            log.info(
                "retrying stream from %s in %.2fs (attempt %d/%d)",
                dispatch.provider_id,
                delay,
                retries,
                self._max_retries,
            )
            await self._sleep(delay)

    async def _fallback(
        self,
        request: ChatRequest,
        *,
        reason: str,
        model: str | None,
        deadline: float,
    ) -> RouteOutcome:
        cloud = self._cloud_provider()
        if cloud is None:
            raise NoAvailableProviderError(
                "local provider unavailable and no cloud provider is registered"
            )
        provider_id, provider = cloud
        if not provider.is_configured():
            raise ProviderUnavailable(
                "cloud fallback unavailable: Groq is not configured"
            )
        return await self._route_cloud(
            request,
            model_override=model,
            reason=reason,
            deadline=deadline,
            cloud_consent="granted",
            provider_id=provider_id,
        )

    # -- Cloud consent + strict isolation ------------------------------------------

    async def _route_cloud(
        self,
        request: ChatRequest,
        *,
        model_override: str | None,
        reason: str,
        deadline: float,
        cloud_consent: str,
        provider_id: str = "groq",
    ) -> RouteOutcome:
        dispatch = await self._prepare_cloud_dispatch(
            request,
            model_override=model_override,
            reason=reason,
            deadline=deadline,
            cloud_consent=cloud_consent,
            provider_id=provider_id,
        )
        outcome = await self._attempt(
            dispatch.provider_id,
            dispatch.request,
            deadline=deadline,
            expected_privacy=PrivacyLevel.CLOUD,
            dispatch_guard=dispatch.dispatch_guard,
        )
        return dataclasses.replace(outcome, cloud_consent=dispatch.cloud_consent)

    async def _prepare_cloud_dispatch(
        self,
        request: ChatRequest,
        *,
        model_override: str | None,
        reason: str,
        deadline: float,
        cloud_consent: str,
        provider_id: str = "groq",
        stream: bool = False,
    ) -> _StreamDispatch:
        """Run the complete Stage 2.4 cloud gate and return one dispatch plan.

        Shared verbatim by non-streaming ``complete()`` and streaming
        ``stream_chat()``: consent, request/fingerprint binding, approved
        system prompt, exactly two messages, and validation of the exact
        serialized HTTP body (including the ``stream`` flag).
        """
        provider = (
            self._providers.get(provider_id)
            if provider_id in self._providers.ids
            else None
        )
        if provider is None:
            raise NoAvailableProviderError("cloud provider is not registered")
        if self._privacy(provider) is not PrivacyLevel.CLOUD:
            raise RoutingError(
                f"provider {provider_id!r} is not a cloud provider"
            )

        self._ensure_network_allowed()
        payload = build_cloud_payload(
            request,
            system_prompt=JARVIS_SYSTEM_PROMPT,
            model_override=model_override,
        )
        preview_request = payload.to_chat_request()
        self._validate_provider(
            provider, preview_request, expected_privacy=PrivacyLevel.CLOUD
        )

        proposal = CloudFallbackProposed(
            request_id=request.request_id,
            reason=reason[:300],
            provider=provider_id,
            model=payload.model or "",
            payload_preview=payload.preview(),
            payload_fingerprint=payload.fingerprint,
            scope=payload.scope_description,
        )
        await self._bus.publish(proposal)

        decision = await self._consent.request_consent(
            request_id=request.request_id,
            proposal=proposal,
            payload=payload,
        )
        await self._audit(
            "cloud_consent",
            result="granted" if decision.granted else "denied",
            approved_by=decision.decided_by,
            detail={
                "request_id": request.request_id,
                "provider": provider_id,
                "scope": payload.scope_description,
            },
        )

        if not decision.granted:
            raise CloudConsentDeniedError(
                f"cloud consent was not granted for request {request.request_id}"
            )
        # Defense in depth: the decision must be bound to THIS request and
        # THIS payload. A UI cannot bypass these checks.
        if (
            decision.request_id != payload.request_id
            or decision.fingerprint != payload.fingerprint
        ):
            raise CloudConsentDeniedError(
                "consent decision is not bound to this request and payload"
            )

        self._ensure_network_allowed()
        if self._clock() >= deadline:
            raise ProviderTimeout("routing request budget exhausted before dispatch")

        try:
            cloud_request = validate_cloud_dispatch(
                payload,
                decision_fingerprint=decision.fingerprint,
                decision_request_id=decision.request_id,
                approved_system_prompt=JARVIS_SYSTEM_PROMPT,
                original_request=request,
            )
            default_model = getattr(self._settings, "groq_model", "")
            body = serialize_cloud_body(
                payload, default_model=default_model, stream=stream
            )
            validate_serialized_cloud_body(
                body,
                payload=payload,
                default_model=default_model,
                stream=stream,
            )
        except PayloadValidationError:
            await self._audit(
                "cloud_payload",
                result="rejected",
                detail={"request_id": request.request_id, "provider": provider_id},
            )
            log.error("cloud payload validation failed; dispatch blocked")
            raise

        # Pin the provider's model explicitly to the same value used to
        # serialize and validate the outgoing body. No content changes.
        dispatch_request = dataclasses.replace(
            cloud_request, model=payload.model or default_model
        )

        def dispatch_guard(candidate: ChatRequest) -> None:
            """Re-verify consent binding + the serialized body immediately
            before every provider attempt (including retries)."""
            verified = validate_cloud_dispatch(
                payload,
                decision_fingerprint=decision.fingerprint,
                decision_request_id=decision.request_id,
                approved_system_prompt=JARVIS_SYSTEM_PROMPT,
                original_request=request,
            )
            if [
                (message.role, message.content) for message in candidate.messages
            ] != [(message.role, message.content) for message in verified.messages]:
                raise PayloadValidationError(
                    "cloud dispatch messages changed after validation"
                )
            if candidate.model != (payload.model or default_model):
                raise PayloadValidationError(
                    "cloud dispatch model changed after validation"
                )
            body = serialize_cloud_body(
                payload, default_model=default_model, stream=stream
            )
            validate_serialized_cloud_body(
                body,
                payload=payload,
                default_model=default_model,
                stream=stream,
            )

        return _StreamDispatch(
            provider_id=provider_id,
            request=dispatch_request,
            expected_privacy=PrivacyLevel.CLOUD,
            cloud_consent=cloud_consent,
            dispatch_guard=dispatch_guard,
        )

    # -- One provider attempt loop (bounded retries) --------------------------------

    async def _attempt(
        self,
        provider_id: str,
        request: ChatRequest,
        *,
        deadline: float,
        expected_privacy: PrivacyLevel,
        dispatch_guard: Callable[[ChatRequest], None] | None = None,
    ) -> RouteOutcome:
        provider = self._providers.get(provider_id)
        self._validate_provider(provider, request, expected_privacy=expected_privacy)

        announced = False
        retries = 0
        while True:
            self._ensure_network_allowed()
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise ProviderTimeout("routing request budget exhausted")

            if not announced:
                await self._publish_selected(provider, request)
                announced = True

            if dispatch_guard is not None:
                dispatch_guard(request)  # validate immediately before dispatch

            attempt_timeout = remaining
            if request.timeout is not None:
                attempt_timeout = min(attempt_timeout, request.timeout)

            try:
                response = await asyncio.wait_for(
                    provider.chat(request), timeout=attempt_timeout
                )
            except asyncio.CancelledError:
                raise  # never retried, never followed by a fallback
            except asyncio.TimeoutError:
                error: ProviderError = ProviderTimeout(
                    f"{provider_id} request timed out"
                )
            except ProviderError as exc:
                error = exc
            except Exception as exc:  # noqa: BLE001 - classify, never leak vendor detail
                error = ProviderUnavailable(
                    f"{provider_id} request failed ({type(exc).__name__})"
                )
            else:
                await self._audit(
                    "llm_route",
                    result="ok",
                    detail={
                        "request_id": request.request_id,
                        "provider": provider_id,
                        "model": response.model,
                        "privacy": str(expected_privacy.value),
                        "retries": retries,
                    },
                )
                return RouteOutcome(
                    response=response,
                    provider=provider_id,
                    model=response.model,
                    privacy=str(expected_privacy.value),
                    request_id=request.request_id,
                    retries=retries,
                    cloud_consent="",
                )

            # Actual failures trigger a health refresh (best effort).
            await self._refresh_health_safely(provider_id)

            if not isinstance(error, _RETRYABLE_ERRORS):
                await self._audit_failure(
                    provider_id, request.request_id, error, retries
                )
                raise error
            if retries >= self._max_retries:
                await self._audit_failure(
                    provider_id, request.request_id, error, retries
                )
                raise error

            delay = self._retry_delay(error, retries)
            if self._clock() + delay >= deadline:
                await self._audit_failure(
                    provider_id, request.request_id, error, retries
                )
                raise error

            retries += 1
            log.info(
                "retrying %s in %.2fs (attempt %d/%d)",
                provider_id,
                delay,
                retries,
                self._max_retries,
            )
            await self._sleep(delay)

    def _retry_delay(self, error: ProviderError, retries: int) -> float:
        if isinstance(error, ProviderRateLimited) and error.retry_after is not None:
            # Provider-advised delay (already clamped by ProviderRateLimited).
            return float(error.retry_after)
        return min(self._backoff_base * (2**retries), self._backoff_max)

    # -- Validation ------------------------------------------------------------------

    def _validate_messages(self, request: ChatRequest) -> None:
        if not request.messages:
            raise ProviderInvalidRequest("chat request has no messages")
        for message in request.messages:
            if message.role not in _ALLOWED_ROLES:
                raise ProviderInvalidRequest(
                    f"unsupported chat message role: {message.role!r}"
                )
        if not any(message.role == "user" for message in request.messages):
            raise ProviderInvalidRequest("chat request has no user message")

    def _validate_provider(
        self,
        provider: LLMProvider,
        request: ChatRequest,
        *,
        expected_privacy: PrivacyLevel,
    ) -> None:
        capabilities = getattr(provider, "capabilities", None)
        if capabilities is None:
            raise RoutingError(f"provider {provider.id!r} has no capabilities")
        if self._privacy(provider) is not expected_privacy:
            raise RoutingError(
                f"provider {provider.id!r} privacy does not match the selected route"
            )
        if not provider.is_configured():
            raise ProviderUnavailable(
                f"provider {provider.id!r} is not configured"
            )
        if any(message.role == "tool" for message in request.messages):
            if not capabilities.tools:
                raise ProviderInvalidRequest(
                    f"provider {provider.id!r} does not support tool messages"
                )
        context_size = int(getattr(capabilities, "context_size", 0) or 0)
        if context_size > 0:
            estimated_tokens = (
                sum(len(message.content) for message in request.messages) // 4
            ) + 1
            if estimated_tokens > context_size:
                raise ProviderInvalidRequest(
                    f"request exceeds {provider.id!r} context size"
                )

    def _privacy(self, provider: LLMProvider) -> PrivacyLevel:
        value = provider.capabilities.privacy
        return value if isinstance(value, PrivacyLevel) else PrivacyLevel(str(value))

    @staticmethod
    def _with_model(request: ChatRequest, model: str | None) -> ChatRequest:
        if model is None or model == request.model:
            return request
        return dataclasses.replace(request, model=model)

    # -- Kill switch / health / events / audit ------------------------------------------

    def _ensure_network_allowed(self) -> None:
        active = False
        if self._kill_switch is not None:
            active = bool(self._kill_switch.active)
        elif self._lifecycle is not None:
            active = bool(self._lifecycle.kill_switch_active)
        if active:
            raise KillSwitchActiveError(
                "emergency stop is engaged; network routing is blocked"
            )

    def _is_fresh_hard_down(self, provider_id: str) -> bool:
        health, age = self._providers.cached_health(provider_id)
        if health is None or health.available:
            return False
        if age > self._hard_down_ttl:
            return False  # stale health never suppresses a real attempt
        if getattr(health, "hard_down", False):
            return True
        detail = (health.detail or "").lower()
        return "unreachable" in detail or "connection refused" in detail

    async def _refresh_health_safely(self, provider_id: str) -> None:
        try:
            self._ensure_network_allowed()
        except KillSwitchActiveError:
            return  # no health traffic while the emergency stop is engaged
        try:
            await self._providers.refresh_health(provider_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - health refresh must never crash routing
            log.warning("health refresh failed for %s", provider_id, exc_info=True)

    async def _publish_selected(
        self, provider: LLMProvider, request: ChatRequest
    ) -> None:
        model = request.model or getattr(provider, "_default_model", "") or ""
        await self._bus.publish(
            ProviderSelected(
                request_id=request.request_id,
                provider=provider.id,
                model=model,
                privacy=str(self._privacy(provider).value),
            )
        )

    async def _audit_failure(
        self,
        provider_id: str,
        request_id: str,
        error: ProviderError,
        retries: int,
    ) -> None:
        await self._audit(
            "llm_route",
            result="failed",
            detail={
                "request_id": request_id,
                "provider": provider_id,
                "error": type(error).__name__,
                "retries": retries,
            },
        )

    async def _audit(
        self,
        action: str,
        *,
        result: str,
        approved_by: str = "n/a",
        detail: dict | None = None,
    ) -> None:
        if self._audit_service is None:
            return
        try:
            await self._audit_service.record_async(
                action,
                result=result,
                approved_by=approved_by,
                detail=detail,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - audit failure must not break routing
            log.warning("audit write failed for %s", action, exc_info=True)
