"""Value types and identifier normalisation for conversation persistence.

Identifier policy (Stage 2.8, Gate A):

The rest of the application already uses ``uuid.uuid4().hex`` — a 32-character
lowercase hexadecimal string with no dashes. That external representation is
preserved. Conversion to ``uuid.UUID`` happens exactly once, here, at the
repository boundary; database UUIDs are converted back to hex for callers.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from jarvis.conversations.errors import InvalidIdentifierError


class MessageStatus(StrEnum):
    """Lifecycle of an assistant generation row."""

    PENDING = "pending"
    STREAMING = "streaming"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class ErrorCategory(StrEnum):
    """Bounded, machine-readable failure category stored in the database.

    Human-readable detail is never persisted; it stays in the UI layer so no
    provider or database text can leak private content into a stored field.
    Use :func:`safe_error_display` to map a persisted category back to a
    deterministic, user-facing explanation after reload.
    """

    PERSISTENCE_ERROR = "persistence_error"
    PROVIDER_ERROR = "provider_error"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"


# Deterministic mapping from persisted ErrorCategory to safe, user-facing
# explanations. No raw provider or diagnostic text is ever surfaced.
_ERROR_DISPLAY: dict[str, str] = {
    ErrorCategory.PERSISTENCE_ERROR: "The conversation could not be saved.",
    ErrorCategory.PROVIDER_ERROR: "The model could not produce a response.",
    ErrorCategory.CANCELLED: "The request was stopped.",
    ErrorCategory.TIMEOUT: "The request timed out.",
    ErrorCategory.INTERRUPTED: "Generation was interrupted.",
    ErrorCategory.UNKNOWN: "An unexpected error occurred.",
}


def safe_error_display(category: str | None) -> str:
    """Return the deterministic user-facing explanation for a persisted category.

    Unknown or ``None`` categories map to a generic message; raw provider or
    diagnostic text is never returned.
    """
    if not category:
        return ""
    return _ERROR_DISPLAY.get(category, _ERROR_DISPLAY[ErrorCategory.UNKNOWN])


TERMINAL_STATUSES = frozenset(
    {
        MessageStatus.COMPLETED,
        MessageStatus.FAILED,
        MessageStatus.CANCELLED,
        MessageStatus.INTERRUPTED,
    }
)

OPEN_STATUSES = frozenset({MessageStatus.PENDING, MessageStatus.STREAMING})


def to_uuid(value: str | uuid.UUID) -> uuid.UUID:
    """Normalise an application identifier to ``uuid.UUID``.

    Accepts the established 32-character hex form, a dashed UUID, or an
    existing ``uuid.UUID``. Raises :class:`InvalidIdentifierError` otherwise.
    """
    if isinstance(value, uuid.UUID):
        return value
    if not isinstance(value, str):
        raise InvalidIdentifierError(
            f"identifier must be str or UUID, not {type(value).__name__}"
        )
    text = value.strip()
    if not text:
        raise InvalidIdentifierError("identifier is empty")
    try:
        return uuid.UUID(text)
    except (ValueError, AttributeError, TypeError) as exc:
        raise InvalidIdentifierError(f"malformed identifier: {text[:48]!r}") from exc


def to_hex(value: str | uuid.UUID | None) -> str | None:
    """Return the application's 32-character hex form (``None`` passthrough)."""
    if value is None:
        return None
    return to_uuid(value).hex


@dataclass(frozen=True)
class PersistedTurn:
    """One durably persisted logical turn.

    ``request_id`` identifies the turn. An original turn owns one user row and
    one assistant row; an explicit retry is assistant-only and carries a new
    request ID plus ``retry_of_request_id``.
    """

    conversation_id: str
    request_id: str
    assistant_message_id: str
    user_message_id: str | None
    user_seq: int | None
    assistant_seq: int
    assistant_status: MessageStatus = MessageStatus.PENDING
    assistant_content_version: int = 0
    created_conversation: bool = False
    reconciled: bool = False
    retry_of_request_id: str | None = None


@dataclass(frozen=True)
class ConversationSummary:
    """Conversation metadata for listing and restoration."""

    id: str
    title: str
    mode: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


@dataclass(frozen=True)
class StoredMessage:
    """A message as loaded from PostgreSQL, in deterministic ``seq`` order."""

    id: str
    conversation_id: str
    role: str
    content: str
    seq: int
    status: str
    created_at: datetime
    updated_at: datetime
    request_id: str | None = None
    error: str | None = None
    provider: str | None = None
    model: str | None = None
    privacy: str | None = None
    tokens: int | None = None
    retry_of_request_id: str | None = None
    content_version: int = 0


__all__ = [
    "ConversationSummary",
    "ErrorCategory",
    "MessageStatus",
    "OPEN_STATUSES",
    "PersistedTurn",
    "StoredMessage",
    "TERMINAL_STATUSES",
    "safe_error_display",
    "to_hex",
    "to_uuid",
]
