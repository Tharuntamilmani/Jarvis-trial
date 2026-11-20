"""Real Groq provider (Stage 2.3).

Async httpx implementation of Groq's OpenAI-compatible chat completions
API with SSE streaming. Cloud provider (privacy: cloud) — the router and
consent gate arrive in Stage 2.4; until then this provider is registered
but the routing layer never selects it automatically.

Security rules enforced here:

- The API key comes from the DPAPI credential store
  (``CredentialStore`` name ``groq_api_key``) — never from .env or code.
- Missing key ⇒ ``available=False, "not configured"`` with **zero HTTP
  requests**.
- The key never appears in logs, exceptions, events, or health details;
  error messages carry status codes and generic descriptions only.

Stage 2.1 contracts honored: typed ``StreamEvent`` terminal-single rule,
monotonic per-request ``seq``, ``request_id`` preserved, cancellation
propagates as ``asyncio.CancelledError`` (HTTP response closed in
cleanup), ``ProviderRateLimited`` carries the parsed, bounded
``Retry-After``. Router-level retry policy deliberately NOT here.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import AsyncIterator

import httpx

from jarvis.config import Settings
from jarvis.intelligence.providers.base import (
    ChatRequest,
    ChatResponse,
    LLMProvider,
    ModelInfo,
    PrivacyLevel,
    ProviderCapabilities,
    ProviderHealth,
    StreamDone,
    StreamError,
    StreamStart,
    Token,
)
from jarvis.intelligence.providers.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderInvalidRequest,
    ProviderResponseError,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)
from jarvis.monitoring.logging import get_logger
from jarvis.security.credentials import CredentialStore

log = get_logger(__name__)

CREDENTIAL_NAME = "groq_api_key"

# Timeout policy — transport-level guards (router budgets arrive in 2.4).
_CONNECT_TIMEOUT = 5.0
_HEALTH_READ_TIMEOUT = 10.0
_CHAT_READ_TIMEOUT = 60.0   # applies between SSE chunks / non-stream wait
_WRITE_TIMEOUT = 10.0
_POOL_TIMEOUT = 5.0


def parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header (integer seconds or HTTP-date).

    Returns raw seconds (unclamped); bounds are applied by
    ``normalize_retry_after`` inside :class:`ProviderRateLimited`.
    """
    if not value:
        return None
    value = value.strip()
    try:
        return float(value)
    except ValueError:
        pass
    try:
        http_date = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if http_date is None:
        return None
    if http_date.tzinfo is None:
        http_date = http_date.replace(tzinfo=timezone.utc)
    delay = (http_date - datetime.now(timezone.utc)).total_seconds()
    return max(delay, 0.0)


class GroqProvider(LLMProvider):
    """Cloud Groq provider (privacy: cloud — consent required upstream)."""

    id = "groq"

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._api_base = getattr(
            settings, "groq_api_base", "https://api.groq.com/openai/v1"
        ).rstrip("/")
        self._default_model = settings.groq_model
        self._credentials = CredentialStore(settings)
        self.capabilities = ProviderCapabilities(
            streaming=True,
            tools=True,
            context_size=128000,
            privacy=PrivacyLevel.CLOUD,
        )
        self._client = httpx.AsyncClient(
            base_url=self._api_base,
            timeout=httpx.Timeout(
                connect=_CONNECT_TIMEOUT,
                read=_CHAT_READ_TIMEOUT,
                write=_WRITE_TIMEOUT,
                pool=_POOL_TIMEOUT,
            ),
            transport=transport,
        )

    # -- Lifecycle -----------------------------------------------------------------

    async def aclose(self) -> None:
        """Close the shared HTTP client (lifecycle shutdown)."""
        await self._client.aclose()

    # -- Credential handling ---------------------------------------------------------

    def is_configured(self) -> bool:
        """True only when a Groq key exists in the DPAPI store.

        Zero-HTTP by design: the router uses this to decide whether cloud
        fallback is even possible without generating health traffic.
        """
        return self._api_key() is not None

    def _api_key(self) -> str | None:
        """Load the DPAPI-stored key. The value NEVER leaves this method
        except into the Authorization header of an outgoing request."""
        try:
            return self._credentials.load_secret(CREDENTIAL_NAME)
        except Exception as exc:  # noqa: BLE001 - decrypt failures are config issues
            log.warning("groq credential store unavailable (%s)", type(exc).__name__)
            return None

    def _auth_headers(self) -> dict[str, str] | None:
        key = self._api_key()
        if key is None:
            return None
        return {"Authorization": f"Bearer {key}"}

    @staticmethod
    def _not_configured() -> ProviderHealth:
        return ProviderHealth(
            available=False,
            detail="not configured (no Groq API key stored)",
        )

    def _request_timeout(self, request: ChatRequest | None = None) -> httpx.Timeout:
        read = _CHAT_READ_TIMEOUT
        if request is not None and request.timeout is not None:
            read = request.timeout
        return httpx.Timeout(
            connect=_CONNECT_TIMEOUT, read=read, write=_WRITE_TIMEOUT, pool=_POOL_TIMEOUT
        )

    # -- Error mapping -------------------------------------------------------------------

    def _map_request_error(self, exc: Exception) -> ProviderError:
        if isinstance(exc, httpx.TimeoutException):
            return ProviderTimeout("Groq request timed out")
        if isinstance(exc, httpx.ConnectError):
            return ProviderUnavailable("Groq service unreachable")
        if isinstance(exc, httpx.InvalidURL):
            return ProviderInvalidRequest("Groq endpoint is not a valid URL")
        return ProviderUnavailable(f"Groq connection failed ({type(exc).__name__})")

    async def _error_for_status(self, response: httpx.Response) -> ProviderError:
        """Map an HTTP error response. Messages are status-based and
        deliberately exclude any response body that could echo secrets."""
        status = response.status_code
        if status in (401, 403):
            return ProviderAuthError(
                "Groq authentication failed — check the stored API key"
            )
        if status == 429:
            return ProviderRateLimited(
                "Groq rate limit reached",
                retry_after=parse_retry_after(response.headers.get("Retry-After")),
            )
        if status == 400:
            return ProviderInvalidRequest(
                "Groq rejected the request (invalid model or parameters)"
            )
        if status == 404:
            return ProviderInvalidRequest("Groq model or endpoint not found")
        return ProviderResponseError(f"Groq returned HTTP {status}")

    # -- Health & model discovery ----------------------------------------------------------

    async def health(self) -> ProviderHealth:
        if self._auth_headers() is None:
            return self._not_configured()  # zero HTTP requests
        try:
            response = await self._client.get(
                "/models",
                headers=self._auth_headers(),
                timeout=httpx.Timeout(_HEALTH_READ_TIMEOUT, connect=_CONNECT_TIMEOUT),
            )
        except Exception as exc:  # noqa: BLE001 - health never raises
            error = self._map_request_error(exc)
            return ProviderHealth(
                available=False,
                detail=error.args[0],
                hard_down=isinstance(exc, httpx.ConnectError),
            )
        if response.status_code == 200:
            try:
                payload = response.json()
                models = tuple(
                    ModelInfo(id=str(item.get("id", "")), name=str(item.get("id", "")))
                    for item in payload.get("data", [])
                    if isinstance(item, dict) and item.get("id")
                )
            except Exception:  # noqa: BLE001
                return ProviderHealth(available=False, detail="malformed response from /models")
            return ProviderHealth(available=True, detail="connected", models=models)
        error = await self._error_for_status(response)
        detail = (
            "authentication failed (stored key rejected)"
            if isinstance(error, ProviderAuthError)
            else (
                f"rate limited (retry after {error.retry_after:.0f}s)"
                if isinstance(error, ProviderRateLimited)
                else error.args[0]
            )
        )
        return ProviderHealth(available=False, detail=detail)

    async def list_models(self) -> list[ModelInfo]:
        """List models reported by the /models endpoint.

        Note: not every returned model is chat-capable (e.g. whisper);
        chat availability is validated at request time (HTTP 400 →
        ProviderInvalidRequest).
        """
        health = await self.health()
        if not health.available:
            raise ProviderUnavailable(health.detail or "Groq unavailable")
        return list(health.models)

    # -- Non-streaming chat -------------------------------------------------------------------

    async def chat(self, request: ChatRequest) -> ChatResponse:
        headers = self._auth_headers()
        if headers is None:
            raise ProviderUnavailable("Groq is not configured (no API key stored)")
        model = request.model or self._default_model
        body: dict[str, object] = {
            "model": model,
            "messages": [
                {"role": m.role, "content": m.content} for m in request.messages
            ],
            "temperature": request.temperature,
            "stream": False,
        }
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        try:
            response = await self._client.post(
                "/chat/completions", json=body, headers=headers,
                timeout=self._request_timeout(request),
            )
        except Exception as exc:  # noqa: BLE001
            raise self._map_request_error(exc) from exc
        if response.status_code != 200:
            raise await self._error_for_status(response)
        try:
            payload = response.json()
            choices = payload.get("choices", [])
            content = choices[0].get("message", {}).get("content", "") if choices else ""
            usage = payload.get("usage", {}) or {}
            finish = choices[0].get("finish_reason", "stop") if choices else "error"
        except Exception as exc:  # noqa: BLE001
            raise ProviderResponseError("malformed response from Groq") from exc
        return ChatResponse(
            content=content,
            model=str(payload.get("model", model)),
            provider=self.id,
            tokens_used=int(usage.get("total_tokens", 0) or 0),
            finish_reason=str(finish),
        )

    # -- Streaming (SSE) ------------------------------------------------------------------------

    async def _stream_events(
        self, request: ChatRequest
    ) -> AsyncIterator[StreamStart | Token | StreamDone | StreamError]:
        headers = self._auth_headers()
        if headers is None:
            raise ProviderUnavailable("Groq is not configured (no API key stored)")
        headers = {**headers, "Accept": "text/event-stream"}
        model = request.model or self._default_model
        body: dict[str, object] = {
            "model": model,
            "messages": [
                {"role": m.role, "content": m.content} for m in request.messages
            ],
            "temperature": request.temperature,
            "stream": True,
        }
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        try:
            http_response = await self._client.send(
                self._client.build_request(
                    "POST", "/chat/completions", json=body, headers=headers
                ),
                stream=True,
            )
        except httpx.HTTPError as exc:
            # Send-phase failure: no stream accepted, no events — raise mapped.
            raise self._map_request_error(exc) from exc

        terminal_emitted = False
        try:
            if http_response.status_code != 200:
                raise await self._error_for_status(http_response)

            yield StreamStart(request_id=request.request_id, model=model)

            seq = 0
            parts: list[str] = []
            tokens_used = 0
            saw_record = False

            async for raw_line in http_response.aiter_lines():
                line = raw_line.strip()
                if not line or line.startswith(":"):
                    continue  # blank line or SSE comment / keep-alive
                if not line.startswith("data:"):
                    raise ProviderResponseError("malformed SSE line from Groq")
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    record = json.loads(data)
                except json.JSONDecodeError as exc:
                    raise ProviderResponseError("malformed SSE record from Groq") from exc
                if not isinstance(record, dict):
                    raise ProviderResponseError("malformed SSE record from Groq")
                saw_record = True

                usage = record.get("usage") or {}
                if usage.get("total_tokens") is not None:
                    tokens_used = int(usage["total_tokens"] or 0)

                choices = record.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {}) or {}
                    chunk = delta.get("content", "") or ""
                    if chunk:
                        parts.append(chunk)
                        yield Token(
                            request_id=request.request_id, seq=seq, text=chunk
                        )
                        seq += 1

            if not saw_record:
                raise ProviderResponseError("Groq stream ended without records")

            terminal_emitted = True
            yield StreamDone(
                request_id=request.request_id,
                content="".join(parts),
                tokens_used=tokens_used,
            )
        except Exception as exc:  # noqa: BLE001 - CancelledError is NOT caught
            if not terminal_emitted:
                mapped = self._map_request_error(exc) if isinstance(
                    exc, httpx.HTTPError
                ) else (
                    exc
                    if isinstance(exc, ProviderError)
                    else ProviderResponseError(f"stream failed ({type(exc).__name__})")
                )
                yield StreamError(request_id=request.request_id, error=mapped)
            else:
                raise
        finally:
            # Runs on exhaustion, on CancelledError (propagates unchanged),
            # and on aclose() — always closing the HTTP response.
            await http_response.aclose()

    def stream(
        self, request: ChatRequest
    ) -> AsyncIterator[StreamStart | Token | StreamDone | StreamError]:
        return self._stream_events(request)
