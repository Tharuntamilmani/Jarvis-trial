"""Strict cloud payload isolation (Plan v1.1 refinement 1).

The cloud provider (Groq) must NEVER receive the original local
``ChatRequest``. This module is the ONLY sanctioned path for building a
cloud request. The cloud payload contains exactly:

1. the approved static JARVIS system prompt (``prompts.py``), and
2. the current user message: the FINAL message of the request, which must
   be a non-empty ``user`` message. Earlier user messages are history and
   are never copied.

Strictly excluded by construction: previous conversation messages, local
memory, retrieved documents, files/attachments, tool outputs, hidden
context, machine info, debug info, environment variables, internal state.

A SHA-256 fingerprint binds the payload so it cannot silently change
between consent and transmission; :func:`validate_cloud_dispatch`
re-verifies everything immediately before provider dispatch.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from jarvis.core.exceptions import JarvisError
from jarvis.intelligence.providers.base import ChatMessage, ChatRequest


class PayloadValidationError(JarvisError):
    """The outbound cloud payload failed isolation validation."""


_ALLOWED_ROLES = frozenset({"system", "user", "assistant", "tool"})


def _validated_current_user_message(request: ChatRequest) -> str:
    """Return the current user message or reject an invalid/ambiguous request.

    The current turn is unambiguously the FINAL message and it must be a
    non-empty ``user`` message. Earlier user messages are history and are
    never copied to the cloud.
    """
    if not request.request_id:
        raise PayloadValidationError(
            "cloud payload requires a non-empty request ID"
        )
    if not request.messages:
        raise PayloadValidationError(
            "cloud payload requires at least one message"
        )
    for message in request.messages:
        if message.role not in _ALLOWED_ROLES:
            raise PayloadValidationError(
                f"unsupported chat message role: {message.role!r}"
            )
    current = request.messages[-1]
    if current.role != "user":
        raise PayloadValidationError(
            "the current user message must be the final message"
        )
    if not current.content.strip():
        raise PayloadValidationError("the current user message is empty")
    return current.content


@dataclass(frozen=True)
class CloudPayload:
    """The sanitized, consent-bound cloud payload."""

    request_id: str
    system_prompt: str
    user_message: str
    model: str | None
    temperature: float
    max_tokens: int | None
    fingerprint: str

    # -- Safe metadata for consent UIs -----------------------------------------

    def preview(self) -> str:
        """Human-safe description of the exact outbound scope."""
        snippet = (
            self.user_message[:80] + "…"
            if len(self.user_message) > 80
            else self.user_message
        )
        return (
            f"system prompt ({len(self.system_prompt)} chars) + "
            f"your message ({len(self.user_message)} chars): “{snippet}”. "
            "No conversation history, memories, files, or hidden context."
        )

    @property
    def scope_description(self) -> str:
        return "system prompt + current user message only (2 messages)"

    # -- The one and only serialized form ---------------------------------------

    def to_chat_request(self) -> ChatRequest:
        """Build the cloud ChatRequest: exactly two messages, fixed order."""
        return ChatRequest(
            model=self.model,
            messages=[
                ChatMessage(role="system", content=self.system_prompt),
                ChatMessage(role="user", content=self.user_message),
            ],
            request_id=self.request_id,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )


def build_cloud_payload(
    request: ChatRequest,
    *,
    system_prompt: str,
    model_override: str | None = None,
) -> CloudPayload:
    """Build a sanitized cloud payload from an original local request.

    Only the final (current) user message is copied; everything else in the
    local request is intentionally dropped. ``model_override`` supports an
    explicit "groq:<model>" selection; ``None`` means "provider default".
    Invalid or ambiguous request shapes are rejected before any consent
    proposal is shown.
    """
    user_message = _validated_current_user_message(request)
    # The cloud model is NEVER inherited from the local request: a local
    # model id (e.g. "qwen3:8b") is meaningless to Groq. ``None`` means the
    # cloud provider's configured default; only an explicit cloud override
    # is copied.
    model = model_override
    fingerprint = _fingerprint(
        request_id=request.request_id,
        system_prompt=system_prompt,
        user_message=user_message,
        model=model if model is not None else "provider-default",
        temperature=request.temperature,
        max_tokens=request.max_tokens,
    )
    return CloudPayload(
        request_id=request.request_id,
        system_prompt=system_prompt,
        user_message=user_message,
        model=model,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        fingerprint=fingerprint,
    )


def validate_cloud_dispatch(
    payload: CloudPayload,
    *,
    decision_fingerprint: str,
    decision_request_id: str,
    approved_system_prompt: str,
    original_request: ChatRequest,
) -> ChatRequest:
    """Validate the payload at the core routing boundary, immediately
    before provider dispatch. Returns the verified ChatRequest to send.

    Checks (all mandatory):
    - system prompt matches the approved prompt exactly,
    - user message matches the last user message of the original request,
    - exactly two messages with system→user ordering,
    - request ID matches the consent decision,
    - payload fingerprint matches the consent decision.
    """
    if payload.system_prompt != approved_system_prompt:
        raise PayloadValidationError("system prompt does not match the approved prompt")

    if original_request.request_id != payload.request_id:
        raise PayloadValidationError(
            "original request ID does not match the consented payload"
        )
    current = _validated_current_user_message(original_request)
    if current != payload.user_message:
        raise PayloadValidationError("user message does not match the current request")

    cloud_request = payload.to_chat_request()
    if len(cloud_request.messages) != 2:
        raise PayloadValidationError("cloud payload must contain exactly two messages")
    if [m.role for m in cloud_request.messages] != ["system", "user"]:
        raise PayloadValidationError("cloud payload roles/order violated")

    if payload.request_id != decision_request_id:
        raise PayloadValidationError("request ID does not match the consent decision")
    if payload.fingerprint != decision_fingerprint:
        raise PayloadValidationError(
            "payload changed after consent (fingerprint mismatch)"
        )
    expected_fingerprint = _fingerprint(
        request_id=payload.request_id,
        system_prompt=payload.system_prompt,
        user_message=payload.user_message,
        model=payload.model if payload.model is not None else "provider-default",
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
    )
    if payload.fingerprint != expected_fingerprint:
        raise PayloadValidationError("payload fingerprint is internally inconsistent")
    return cloud_request


# --- Serialized HTTP body validation ------------------------------------------
#
# The router validates the exact serialized OpenAI-compatible body that the
# Groq provider will transmit, immediately before dispatch. The body schema
# mirrors ``GroqProvider.chat`` (non-streaming) exactly; a parity test asserts
# that mirrored serialization and the captured HTTP body are identical.

CLOUD_BODY_KEYS = frozenset(
    {"model", "messages", "temperature", "stream", "max_tokens"}
)
CLOUD_MESSAGE_KEYS = frozenset({"role", "content"})


def serialize_cloud_body(
    payload: CloudPayload,
    *,
    default_model: str,
    stream: bool = False,
) -> dict[str, object]:
    """Serialize the exact cloud HTTP body from the sanitized payload.

    ``payload.model`` ``None`` resolves to the provider default, matching
    the provider's own request construction.
    """
    model = payload.model if payload.model is not None else default_model
    body: dict[str, object] = {
        "model": model,
        "messages": [
            {"role": "system", "content": payload.system_prompt},
            {"role": "user", "content": payload.user_message},
        ],
        "temperature": payload.temperature,
        "stream": stream,
    }
    if payload.max_tokens is not None:
        body["max_tokens"] = payload.max_tokens
    return body


def validate_serialized_cloud_body(
    body: object,
    *,
    payload: CloudPayload,
    default_model: str,
    stream: bool = False,
) -> None:
    """Validate the actual outbound HTTP body against the consent payload.

    Rejects unknown keys, missing keys, extra messages, role/content
    mismatches, model drift, and any serialization round-trip difference.
    """
    if not isinstance(body, dict):
        raise PayloadValidationError("cloud HTTP body must be a JSON object")

    extra = set(body) - CLOUD_BODY_KEYS
    if extra:
        raise PayloadValidationError(
            f"cloud HTTP body contains unsupported fields: {sorted(extra)}"
        )
    required = {"model", "messages", "temperature", "stream"}
    if not required <= set(body):
        raise PayloadValidationError("cloud HTTP body is missing required fields")

    expected_model = payload.model if payload.model is not None else default_model
    if body.get("model") != expected_model:
        raise PayloadValidationError("cloud HTTP body model does not match consent")

    messages = body.get("messages")
    if not isinstance(messages, list) or len(messages) != 2:
        raise PayloadValidationError(
            "cloud HTTP body must contain exactly two messages"
        )
    expected = (
        ("system", payload.system_prompt),
        ("user", payload.user_message),
    )
    for message, (role, content) in zip(messages, expected, strict=True):
        if not isinstance(message, dict):
            raise PayloadValidationError("cloud HTTP body message is not an object")
        if set(message) != CLOUD_MESSAGE_KEYS:
            raise PayloadValidationError(
                "cloud HTTP body message contains unsupported fields"
            )
        if message.get("role") != role:
            raise PayloadValidationError("cloud HTTP body role/order violated")
        if message.get("content") != content:
            raise PayloadValidationError(
                "cloud HTTP body content does not match consent"
            )

    if body.get("temperature") != payload.temperature:
        raise PayloadValidationError("cloud HTTP body temperature does not match consent")
    if body.get("stream") is not stream:
        raise PayloadValidationError("cloud HTTP body stream flag does not match mode")
    if payload.max_tokens is None:
        if "max_tokens" in body:
            raise PayloadValidationError("cloud HTTP body has unexpected max_tokens")
    elif body.get("max_tokens") != payload.max_tokens:
        raise PayloadValidationError("cloud HTTP body max_tokens does not match consent")

    # Prove the JSON round-trip preserves exactly the validated scope.
    try:
        round_tripped = json.loads(
            json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        )
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise PayloadValidationError("cloud HTTP body is not serializable") from exc
    if round_tripped != body:
        raise PayloadValidationError("cloud HTTP body serialization drifted")


def _fingerprint(
    *,
    request_id: str,
    system_prompt: str,
    user_message: str,
    model: str,
    temperature: float,
    max_tokens: int | None,
) -> str:
    canonical = json.dumps(
        {
            "request_id": request_id,
            "system": system_prompt,
            "user": user_message,
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
