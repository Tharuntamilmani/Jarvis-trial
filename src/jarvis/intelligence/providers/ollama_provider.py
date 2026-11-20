"""Real Ollama provider (Stage 2.2).

Async httpx implementation of Ollama's native chat API
(``POST /api/chat``, NDJSON streaming). Honors the Stage 2.1 contracts:

- typed :data:`StreamEvent` streams (``StreamStart`` → ``Token``* → one
  terminal event),
- monotonic per-request ``seq``,
- ``request_id`` preserved in every event,
- ``asyncio.CancelledError`` never converted into ``StreamError`` — the
  HTTP response is closed in the generator cleanup and cancellation
  propagates unchanged.

No credentials are involved (local service); error messages never embed
secrets or full URLs.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from jarvis.config import Settings
from jarvis.intelligence.providers.base import (
    ChatMessage,
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
    ProviderError,
    ProviderInvalidRequest,
    ProviderResponseError,
    ProviderTimeout,
    ProviderUnavailable,
)
from jarvis.monitoring.logging import get_logger

log = get_logger(__name__)

# Timeout policy (Plan v1.1 correction 8). Router-level budgets (2.4) wrap
# these; these are transport-level guards.
_CONNECT_TIMEOUT = 5.0
_HEALTH_TIMEOUT = 3.0
_CHAT_READ_TIMEOUT = 90.0   # applies between stream chunks / non-stream wait
_WRITE_TIMEOUT = 10.0
_POOL_TIMEOUT = 5.0


class OllamaProvider(LLMProvider):
    """Local Ollama provider (privacy: local — data never leaves the machine)."""

    id = "ollama"

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._host = settings.ollama_host.rstrip("/")
        self._default_model = settings.ollama_model
        self._keep_alive = getattr(settings, "ollama_keep_alive", "10m")
        # Thinking-model control (qwen3 etc.). With think disabled the
        # answer channel streams directly; enabled models otherwise spend
        # the token budget on a separate "thinking" channel first.
        self._think = getattr(settings, "ollama_think", False)
        self.capabilities = ProviderCapabilities(
            streaming=True,
            tools=True,
            context_size=8192,
            privacy=PrivacyLevel.LOCAL,
        )
        self._client = httpx.AsyncClient(
            base_url=self._host,
            timeout=httpx.Timeout(
                connect=_CONNECT_TIMEOUT,
                read=_CHAT_READ_TIMEOUT,
                write=_WRITE_TIMEOUT,
                pool=_POOL_TIMEOUT,
            ),
            transport=transport,
        )

    # -- Lifecycle ----------------------------------------------------------------

    async def aclose(self) -> None:
        """Close the shared HTTP client (lifecycle shutdown)."""
        await self._client.aclose()

    # -- Helpers ---------------------------------------------------------------------

    def _resolve_model(self, model: str | None) -> str:
        return model or self._default_model

    @staticmethod
    def _is_think_rejection(detail: str) -> bool:
        """True when the model rejected the ``think`` field (non-thinking
        model on newer Ollama). Enables one graceful retry without it."""
        lowered = detail.lower()
        return "think" in lowered and (
            "does not support" in lowered or "not support" in lowered
        )

    def _build_body(
        self, request: ChatRequest, *, stream: bool, include_think: bool
    ) -> dict[str, object]:
        model = self._resolve_model(request.model)
        body: dict[str, object] = {
            "model": model,
            "messages": self._messages_payload(request.messages),
            "stream": stream,
            "keep_alive": self._keep_alive,
            "options": {"temperature": request.temperature},
        }
        if request.max_tokens is not None:
            body["options"] = {  # type: ignore[index]
                "temperature": request.temperature,
                "num_predict": request.max_tokens,
            }
        if include_think:
            body["think"] = self._think
        return body

    def _messages_payload(self, messages: list[ChatMessage]) -> list[dict[str, str]]:
        return [{"role": m.role, "content": m.content} for m in messages]

    def _map_request_error(self, exc: Exception) -> ProviderError:
        if isinstance(exc, httpx.TimeoutException):
            return ProviderTimeout("Ollama request timed out")
        if isinstance(exc, httpx.ConnectError):
            return ProviderUnavailable("Ollama service unreachable")
        if isinstance(exc, httpx.UnsupportedProtocol):
            return ProviderUnavailable("Ollama endpoint is misconfigured")
        if isinstance(exc, httpx.InvalidURL):
            return ProviderInvalidRequest("Ollama endpoint is not a valid URL")
        return ProviderUnavailable(f"Ollama connection failed ({type(exc).__name__})")

    async def _error_for_status(self, response: httpx.Response) -> ProviderError:
        detail = ""
        try:
            body = response.json()
            detail = str(body.get("error", "")) if isinstance(body, dict) else ""
        except Exception:  # noqa: BLE001 - non-JSON error bodies are fine
            detail = ""
        if response.status_code == 404:
            return ProviderInvalidRequest(
                f"Ollama model not found{': ' + detail if detail else ''}"
            )
        if response.status_code == 400:
            return ProviderInvalidRequest(
                f"Ollama rejected the request{': ' + detail if detail else ''}"
            )
        return ProviderResponseError(
            f"Ollama returned HTTP {response.status_code}"
            f"{': ' + detail if detail else ''}"
        )

    # -- Health & model discovery ---------------------------------------------------

    async def health(self) -> ProviderHealth:
        try:
            response = await self._client.get(
                "/api/tags", timeout=httpx.Timeout(_HEALTH_TIMEOUT)
            )
        except Exception as exc:  # noqa: BLE001 - health never raises
            error = self._map_request_error(exc)
            return ProviderHealth(
                available=False,
                detail=error.args[0],
                hard_down=isinstance(exc, httpx.ConnectError),
            )
        if response.status_code != 200:
            error = await self._error_for_status(response)
            return ProviderHealth(available=False, detail=error.args[0])
        try:
            payload = response.json()
            models = tuple(
                ModelInfo(
                    id=item.get("name", ""),
                    name=item.get("name", ""),
                    context_size=int(item.get("context_size", 0) or 0),
                )
                for item in payload.get("models", [])
            )
        except Exception:  # noqa: BLE001
            return ProviderHealth(available=False, detail="malformed response from /api/tags")
        return ProviderHealth(available=True, detail="connected", models=models)

    async def list_models(self) -> list[ModelInfo]:
        health = await self.health()
        if not health.available:
            raise ProviderUnavailable(health.detail or "Ollama unavailable")
        return list(health.models)

    # -- Non-streaming chat ------------------------------------------------------------

    async def chat(self, request: ChatRequest) -> ChatResponse:
        body = self._build_body(request, stream=False, include_think=True)
        try:
            response = await self._client.post("/api/chat", json=body)
        except Exception as exc:  # noqa: BLE001
            raise self._map_request_error(exc) from exc
        if response.status_code == 400:
            detail = ""
            try:
                payload = response.json()
                detail = str(payload.get("error", "")) if isinstance(payload, dict) else ""
            except Exception:  # noqa: BLE001
                pass
            if self._is_think_rejection(detail):
                # Non-thinking model: retry once without the think field.
                body = self._build_body(request, stream=False, include_think=False)
                try:
                    response = await self._client.post("/api/chat", json=body)
                except Exception as exc:  # noqa: BLE001
                    raise self._map_request_error(exc) from exc
        if response.status_code != 200:
            raise await self._error_for_status(response)
        try:
            payload = response.json()
            content = payload.get("message", {}).get("content", "")
            eval_count = int(payload.get("eval_count", 0) or 0)
        except Exception as exc:  # noqa: BLE001
            raise ProviderResponseError("malformed response from Ollama") from exc
        return ChatResponse(
            content=content,
            model=str(payload.get("model", self._resolve_model(request.model))),
            provider=self.id,
            tokens_used=eval_count,
        )

    # -- Streaming -------------------------------------------------------------------------

    async def _stream_events(
        self, request: ChatRequest
    ) -> AsyncIterator[StreamStart | Token | StreamDone | StreamError]:
        model = self._resolve_model(request.model)
        body = self._build_body(request, stream=True, include_think=True)
        try:
            http_response = await self._client.send(
                self._client.build_request("POST", "/api/chat", json=body),
                stream=True,
            )
            if http_response.status_code == 400:
                detail = ""
                try:
                    payload = http_response.json()
                    detail = (
                        str(payload.get("error", ""))
                        if isinstance(payload, dict)
                        else ""
                    )
                except Exception:  # noqa: BLE001
                    pass
                if self._is_think_rejection(detail):
                    # Non-thinking model: retry once without the think field.
                    await http_response.aclose()
                    body = self._build_body(
                        request, stream=True, include_think=False
                    )
                    http_response = await self._client.send(
                        self._client.build_request("POST", "/api/chat", json=body),
                        stream=True,
                    )
        except httpx.HTTPError as exc:
            # Connection-level failure: no stream was accepted, so no
            # events — raise the mapped error directly.
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

            async for line in http_response.aiter_lines():
                line = line.strip()
                if not line:
                    continue  # tolerate empty keep-alive lines
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ProviderResponseError(
                        "malformed NDJSON line from Ollama"
                    ) from exc
                if not isinstance(record, dict):
                    raise ProviderResponseError("malformed NDJSON line from Ollama")
                saw_record = True

                done = bool(record.get("done", False))
                chunk = record.get("message", {}).get("content", "")
                if chunk:
                    parts.append(chunk)
                    yield Token(request_id=request.request_id, seq=seq, text=chunk)
                    seq += 1
                if done:
                    tokens_used = int(record.get("eval_count", 0) or 0)
                    break

            if not saw_record:
                # A stream that closes without any record is a broken
                # stream, not a successful empty completion.
                raise ProviderResponseError("Ollama stream ended without records")

            terminal_emitted = True
            yield StreamDone(
                request_id=request.request_id,
                content="".join(parts),
                tokens_used=tokens_used,
            )
        except Exception as exc:  # noqa: BLE001
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
            # Runs on exhaustion, on CancelledError (which propagates
            # unchanged), and on aclose() — closing the HTTP response.
            await http_response.aclose()

    def stream(self, request: ChatRequest) -> AsyncIterator[StreamStart | Token | StreamDone | StreamError]:
        return self._stream_events(request)
