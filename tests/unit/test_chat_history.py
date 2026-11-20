"""Local conversation history truncation (Stage 2.6)."""

from __future__ import annotations

from datetime import datetime

from jarvis.desktop.viewmodels.chat_models import (
    MAX_HISTORY_CHARS,
    MAX_HISTORY_MESSAGES,
    STATUS_COMPLETED,
    STATUS_STREAMING,
    ConversationMessage,
    build_local_history,
)
from jarvis.intelligence.cloud_payload import build_cloud_payload
from jarvis.intelligence.prompts import JARVIS_SYSTEM_PROMPT
from jarvis.intelligence.providers.base import ChatRequest


def _message(role: str, content: str, status: str = STATUS_COMPLETED):
    return ConversationMessage(
        id=f"{role}-{content[:8]}-{len(content)}",
        role=role,
        content=content,
        created_at=datetime.now(),
        status=status,
    )


def test_history_includes_conversation_but_not_streaming_placeholder() -> None:
    messages = [
        _message("user", "first question"),
        _message("assistant", "first answer"),
        _message("user", "second question"),
        _message("assistant", "", status=STATUS_STREAMING),
    ]
    history = build_local_history(messages)
    assert [(m.role, m.content) for m in history] == [
        ("user", "first question"),
        ("assistant", "first answer"),
        ("user", "second question"),
    ]


def test_history_is_bounded_to_twenty_messages() -> None:
    messages = []
    for index in range(30):
        messages.append(_message("user", f"question {index}"))
        messages.append(_message("assistant", f"answer {index}"))
    history = build_local_history(messages)
    assert len(history) <= MAX_HISTORY_MESSAGES
    assert history[0].role == "user"
    assert history[-1].content == "answer 29"


def test_newest_user_message_survives_character_limit() -> None:
    huge_question = "x" * (MAX_HISTORY_CHARS + 500)
    messages = [
        _message("user", "old question"),
        _message("assistant", "old answer"),
        _message("user", huge_question),
    ]
    history = build_local_history(messages)
    assert history[-1].content == huge_question
    assert len(history) == 1


def test_history_never_starts_with_assistant_message() -> None:
    messages = [
        _message("assistant", "orphan answer"),
        _message("user", "question"),
        _message("assistant", "answer"),
    ]
    history = build_local_history(messages)
    assert history
    assert history[0].role == "user"
    assert history[0].content == "question"


def test_blank_messages_are_ignored() -> None:
    messages = [
        _message("user", "   "),
        _message("assistant", ""),
        _message("user", "real question"),
    ]
    history = build_local_history(messages)
    assert [(m.role, m.content) for m in history] == [("user", "real question")]


def test_cloud_payload_from_local_history_stays_two_messages_only() -> None:
    """The Stage 2.4 isolation rule still holds for the new local history."""
    messages = [
        _message("user", "private first question"),
        _message("assistant", "private first answer"),
        _message("user", "private second question"),
        _message("assistant", "private second answer"),
        _message("user", "current question"),
    ]
    history = build_local_history(messages)
    assert len(history) == 5  # Ollama receives the local conversation

    request = ChatRequest(
        model=None,
        messages=history,
        request_id="privacy-1",
    )
    payload = build_cloud_payload(
        request, system_prompt=JARVIS_SYSTEM_PROMPT
    )
    cloud_request = payload.to_chat_request()

    assert len(cloud_request.messages) == 2
    assert [m.role for m in cloud_request.messages] == ["system", "user"]
    assert cloud_request.messages[0].content == JARVIS_SYSTEM_PROMPT
    assert cloud_request.messages[1].content == "current question"
    serialized = " ".join(m.content for m in cloud_request.messages)
    for private in (
        "private first question",
        "private first answer",
        "private second question",
        "private second answer",
    ):
        assert private not in serialized
