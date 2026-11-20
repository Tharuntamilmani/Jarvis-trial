"""Strict cloud payload isolation tests (Stage 2.4).

The cloud request may contain exactly the approved system prompt and the
current user message — never history, memories, files, tool output, local
state, or machine details. The serialized HTTP body is validated too.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from jarvis.intelligence.cloud_payload import (
    PayloadValidationError,
    build_cloud_payload,
    serialize_cloud_body,
    validate_cloud_dispatch,
    validate_serialized_cloud_body,
)
from jarvis.intelligence.prompts import JARVIS_SYSTEM_PROMPT
from jarvis.intelligence.providers.base import ChatMessage, ChatRequest

MACHINE_SECRET = "C:\\Users\\Tharun\\secret-file.txt"
MEMORY_SECRET = "memory: the user's private address is 42 Hidden Lane"
TOOL_SECRET = "tool-output: private shell output"
HIDDEN_SECRET = "hidden local system context"


def _local_request(
    *,
    request_id: str = "req-p1",
    user_message: str = "current question",
    model: str | None = "qwen3:8b",
    temperature: float = 0.4,
    max_tokens: int | None = 256,
) -> ChatRequest:
    return ChatRequest(
        model=model,
        messages=[
            ChatMessage("system", HIDDEN_SECRET),
            ChatMessage("user", "old question"),
            ChatMessage("assistant", "old answer"),
            ChatMessage("tool", TOOL_SECRET),
            ChatMessage("system", MEMORY_SECRET),
            ChatMessage("user", f"earlier question mentioning {MACHINE_SECRET}"),
            ChatMessage("user", user_message),
        ],
        request_id=request_id,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def _payload(**kwargs):
    return build_cloud_payload(
        _local_request(**kwargs), system_prompt=JARVIS_SYSTEM_PROMPT
    )


# -- Payload construction -------------------------------------------------------


def test_payload_contains_only_current_user_message() -> None:
    payload = _payload()
    assert payload.system_prompt == JARVIS_SYSTEM_PROMPT
    assert payload.user_message == "current question"
    assert payload.request_id == "req-p1"
    assert payload.model is None  # local model never leaks to cloud


def test_local_model_never_inherited_by_cloud() -> None:
    payload = _payload(model="qwen3:8b")
    assert payload.model is None
    assert "qwen3" not in json.dumps(
        serialize_cloud_body(payload, default_model="llama-3.3-70b-versatile")
    )


def test_explicit_cloud_model_override() -> None:
    request = _local_request()
    payload = build_cloud_payload(
        request,
        system_prompt=JARVIS_SYSTEM_PROMPT,
        model_override="llama-3.1-8b-instant",
    )
    assert payload.model == "llama-3.1-8b-instant"


def test_chat_request_has_exactly_two_messages() -> None:
    cloud_request = _payload().to_chat_request()
    assert len(cloud_request.messages) == 2
    assert [m.role for m in cloud_request.messages] == ["system", "user"]
    assert cloud_request.messages[0].content == JARVIS_SYSTEM_PROMPT
    assert cloud_request.messages[1].content == "current question"


def test_no_history_memory_files_or_tool_output_in_payload() -> None:
    serialized = json.dumps(
        serialize_cloud_body(_payload(), default_model="llama-3.3-70b-versatile")
    )
    for forbidden in (
        "old question",
        "old answer",
        HIDDEN_SECRET,
        MEMORY_SECRET,
        TOOL_SECRET,
        MACHINE_SECRET,
    ):
        assert forbidden not in serialized


def test_missing_user_message_rejected() -> None:
    request = ChatRequest(
        model="qwen3:8b",
        messages=[ChatMessage("system", HIDDEN_SECRET)],
        request_id="req-no-user",
    )
    with pytest.raises(PayloadValidationError):
        build_cloud_payload(request, system_prompt=JARVIS_SYSTEM_PROMPT)


def test_missing_request_id_rejected() -> None:
    request = ChatRequest(
        model="qwen3:8b",
        messages=[ChatMessage("user", "current question")],
        request_id="",
    )
    with pytest.raises(PayloadValidationError, match="request ID"):
        build_cloud_payload(request, system_prompt=JARVIS_SYSTEM_PROMPT)


def test_empty_message_list_rejected() -> None:
    request = ChatRequest(model="qwen3:8b", messages=[], request_id="req-empty")
    with pytest.raises(PayloadValidationError, match="at least one message"):
        build_cloud_payload(request, system_prompt=JARVIS_SYSTEM_PROMPT)


def test_unsupported_role_rejected() -> None:
    request = ChatRequest(
        model="qwen3:8b",
        messages=[
            ChatMessage("admin", "not a real role"),
            ChatMessage("user", "current question"),
        ],
        request_id="req-role",
    )
    with pytest.raises(PayloadValidationError, match="unsupported.*role"):
        build_cloud_payload(request, system_prompt=JARVIS_SYSTEM_PROMPT)


def test_current_user_message_must_be_final() -> None:
    request = ChatRequest(
        model="qwen3:8b",
        messages=[
            ChatMessage("user", "old question"),
            ChatMessage("user", "current question"),
            ChatMessage("assistant", "trailing reply"),
        ],
        request_id="req-trailing",
    )
    with pytest.raises(PayloadValidationError, match="final message"):
        build_cloud_payload(request, system_prompt=JARVIS_SYSTEM_PROMPT)


def test_empty_current_user_message_rejected() -> None:
    request = ChatRequest(
        model="qwen3:8b",
        messages=[ChatMessage("user", "")],
        request_id="req-empty-user",
    )
    with pytest.raises(PayloadValidationError, match="empty"):
        build_cloud_payload(request, system_prompt=JARVIS_SYSTEM_PROMPT)


def test_whitespace_current_user_message_rejected() -> None:
    request = ChatRequest(
        model="qwen3:8b",
        messages=[ChatMessage("user", "   \n\t ")],
        request_id="req-blank-user",
    )
    with pytest.raises(PayloadValidationError, match="empty"):
        build_cloud_payload(request, system_prompt=JARVIS_SYSTEM_PROMPT)


# -- Consent-bound dispatch validation ----------------------------------------


def _dispatch(payload, original=None, *, fingerprint=None, request_id=None):
    original = original or _local_request()
    return validate_cloud_dispatch(
        payload,
        decision_fingerprint=payload.fingerprint if fingerprint is None else fingerprint,
        decision_request_id=payload.request_id if request_id is None else request_id,
        approved_system_prompt=JARVIS_SYSTEM_PROMPT,
        original_request=original,
    )


def test_valid_dispatch_returns_two_message_request() -> None:
    payload = _payload()
    cloud_request = _dispatch(payload)
    assert [m.role for m in cloud_request.messages] == ["system", "user"]
    assert cloud_request.request_id == "req-p1"


def test_system_prompt_drift_rejected() -> None:
    payload = _payload()
    with pytest.raises(PayloadValidationError, match="approved prompt"):
        validate_cloud_dispatch(
            payload,
            decision_fingerprint=payload.fingerprint,
            decision_request_id=payload.request_id,
            approved_system_prompt="A different cloud prompt",
            original_request=_local_request(),
        )


def test_changed_current_message_rejected() -> None:
    payload = _payload()
    changed = _local_request(user_message="different question")
    with pytest.raises(PayloadValidationError, match="current request"):
        _dispatch(payload, changed)


def test_request_id_binding_enforced() -> None:
    payload = _payload()
    with pytest.raises(PayloadValidationError, match="request ID"):
        _dispatch(payload, request_id="req-other")


def test_fingerprint_binding_enforced() -> None:
    payload = _payload()
    with pytest.raises(PayloadValidationError, match="fingerprint"):
        _dispatch(payload, fingerprint="0" * 64)


def test_payload_tampering_detected_by_recomputed_fingerprint() -> None:
    payload = _payload()
    tampered = dataclasses.replace(payload, max_tokens=9999)
    with pytest.raises(PayloadValidationError, match="internally inconsistent"):
        _dispatch(tampered)


def test_payload_user_message_tamper_detected() -> None:
    payload = _payload()
    tampered = dataclasses.replace(payload, user_message="exfiltrate secrets")
    with pytest.raises(PayloadValidationError):
        _dispatch(tampered)


def test_dispatch_rejects_original_request_id_mismatch() -> None:
    payload = _payload()
    original = _local_request(request_id="req-different")
    with pytest.raises(PayloadValidationError, match="original request ID"):
        _dispatch(payload, original)


def test_dispatch_rejects_original_request_without_final_user_message() -> None:
    payload = _payload()
    original = ChatRequest(
        model="qwen3:8b",
        messages=[
            ChatMessage("user", "current question"),
            ChatMessage("assistant", "trailing reply"),
        ],
        request_id=payload.request_id,
    )
    with pytest.raises(PayloadValidationError, match="final message"):
        _dispatch(payload, original)


def test_dispatch_rejects_empty_current_message() -> None:
    payload = _payload()
    original = ChatRequest(
        model="qwen3:8b",
        messages=[ChatMessage("user", "   ")],
        request_id=payload.request_id,
    )
    with pytest.raises(PayloadValidationError, match="empty"):
        _dispatch(payload, original)


# -- Actual serialized HTTP body ----------------------------------------------


def test_serialized_body_is_exactly_two_messages() -> None:
    payload = _payload()
    body = serialize_cloud_body(payload, default_model="llama-3.3-70b-versatile")
    assert set(body) == {"model", "messages", "temperature", "stream", "max_tokens"}
    assert body["model"] == "llama-3.3-70b-versatile"
    assert body["stream"] is False
    assert body["messages"] == [
        {"role": "system", "content": JARVIS_SYSTEM_PROMPT},
        {"role": "user", "content": "current question"},
    ]
    validate_serialized_cloud_body(
        body, payload=payload, default_model="llama-3.3-70b-versatile"
    )


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda b: b.update(extra="leak"), id="extra-key"),
        pytest.param(lambda b: b.pop("temperature"), id="missing-key"),
        pytest.param(
            lambda b: b["messages"].append({"role": "user", "content": "history"}),
            id="extra-message",
        ),
        pytest.param(
            lambda b: b["messages"].reverse(), id="role-order"
        ),
        pytest.param(
            lambda b: b["messages"][1].update(content="changed"), id="content"
        ),
        pytest.param(
            lambda b: b["messages"][1].update(extra="leak"), id="message-extra-key"
        ),
        pytest.param(lambda b: b.update(model="evil-model"), id="model-drift"),
        pytest.param(lambda b: b.update(stream=True), id="stream-drift"),
        pytest.param(lambda b: b.update(max_tokens=1), id="max-tokens-drift"),
        pytest.param(lambda b: b.update(temperature=0.0), id="temperature-drift"),
        pytest.param(lambda b: b.update(messages="not-a-list"), id="not-a-list"),
        pytest.param(lambda b: b.update(messages=[b["messages"][0]]), id="one-message"),
    ],
)
def test_serialized_body_tampering_rejected(mutate) -> None:
    payload = _payload()
    body = serialize_cloud_body(payload, default_model="llama-3.3-70b-versatile")
    mutate(body)
    with pytest.raises(PayloadValidationError):
        validate_serialized_cloud_body(
            body, payload=payload, default_model="llama-3.3-70b-versatile"
        )


def test_max_tokens_omitted_when_not_requested() -> None:
    payload = _payload(max_tokens=None)
    body = serialize_cloud_body(payload, default_model="llama-3.3-70b-versatile")
    assert "max_tokens" not in body
    validate_serialized_cloud_body(
        body, payload=payload, default_model="llama-3.3-70b-versatile"
    )


def test_non_object_body_rejected() -> None:
    payload = _payload()
    with pytest.raises(PayloadValidationError):
        validate_serialized_cloud_body(
            ["not", "a", "body"],
            payload=payload,
            default_model="llama-3.3-70b-versatile",
        )
