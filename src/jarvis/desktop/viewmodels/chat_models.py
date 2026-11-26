"""Headless conversation models and history truncation for Stage 2.6.

The models are plain dataclasses (no Qt) so the controller logic and its
tests stay independent of widget rendering. History building is the only
place where the in-memory conversation is converted into provider
messages; cloud isolation is still enforced exclusively by the router.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from jarvis.intelligence.providers.base import ChatMessage

MAX_HISTORY_MESSAGES = 20
MAX_HISTORY_CHARS = 12_000

# Message status values shared by the controller and the view.
STATUS_PENDING = "pending"
STATUS_STREAMING = "streaming"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_INTERRUPTED = "interrupted"

# Statuses that must never be supplied to a provider as a finished answer.
# ``pending`` and ``interrupted`` were added in Stage 2.8; ``interrupted``
# content is a preserved partial response, not a completed assistant turn.
NON_HISTORY_STATUSES = frozenset(
    {STATUS_PENDING, STATUS_STREAMING, STATUS_INTERRUPTED}
)


@dataclass
class ConversationMessage:
    """One message in the in-memory conversation.

    ``content`` always holds every token accepted so far; terminal events
    replace it with the authoritative content from the core service.
    """

    id: str
    role: str  # user | assistant
    content: str
    created_at: datetime
    status: str = STATUS_COMPLETED
    provider: str = ""
    model: str = ""
    privacy: str = ""
    error: str = ""
    # Machine-readable category kept separate from the human-readable detail
    # above (Stage 2.8). Only the category is ever persisted.
    error_category: str = ""
    tokens_used: int = 0
    request_id: str = ""


@dataclass(frozen=True)
class ConsentRequest:
    """Exact, safe metadata shown in the cloud-consent card.

    Built only from the router's proposal and the sanitized
    :class:`~jarvis.intelligence.cloud_payload.CloudPayload`: the visible
    system prompt and user message are exactly what the router validated.
    """

    request_id: str
    provider: str
    model: str
    reason: str
    system_prompt: str
    user_message: str
    scope: str
    fingerprint: str
    created_at: datetime = field(default_factory=datetime.now)


def build_local_history(
    messages: Sequence[ConversationMessage],
) -> list[ChatMessage]:
    """Build the local (Ollama) history with bounded size.

    Rules:
    - only user/assistant messages with non-empty content are eligible;
    - at most :data:`MAX_HISTORY_MESSAGES` messages;
    - roughly :data:`MAX_HISTORY_CHARS` characters total;
    - the newest user message is ALWAYS retained, even if oversized;
    - retained history never starts with an assistant message.

    The in-flight assistant placeholder is excluded by its ``streaming``
    status. This affects only the local request; the router still builds
    the cloud payload from the current user message alone.
    """

    eligible = [
        message
        for message in messages
        if message.role in ("user", "assistant")
        and message.content.strip()
        and message.status not in NON_HISTORY_STATUSES
    ][-MAX_HISTORY_MESSAGES:]
    if not eligible:
        return []

    newest_user_index: int | None = None
    for index in range(len(eligible) - 1, -1, -1):
        if eligible[index].role == "user":
            newest_user_index = index
            break
    if newest_user_index is None:
        return []

    selected: list[ConversationMessage] = []
    total_chars = 0
    for index in range(len(eligible) - 1, -1, -1):
        message = eligible[index]
        length = len(message.content)
        if index == newest_user_index:
            # Oversized current input is never dropped.
            selected.append(message)
            total_chars += length
            continue
        if total_chars + length > MAX_HISTORY_CHARS:
            break
        selected.append(message)
        total_chars += length

    selected.reverse()
    # A local conversation must begin with the user; leading assistant
    # messages are dropped if the trim boundary lands inside a turn.
    while selected and selected[0].role != "user":
        selected.pop(0)

    return [
        ChatMessage(role=message.role, content=message.content)
        for message in selected
    ]
