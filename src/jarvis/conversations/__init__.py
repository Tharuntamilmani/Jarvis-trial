"""Persistent conversation subsystem (Stage 2.8, Gate A).

This package is core-level: it must never import PySide6. The desktop layer
reaches it through the service registry as ``"conversations"``.
"""

from jarvis.conversations.errors import (
    ConversationError,
    ConversationNotFoundError,
    InvalidIdentifierError,
    PersistenceError,
    PersistenceTimeoutError,
    PersistenceUnconfirmedError,
)
from jarvis.conversations.models import (
    ConversationSummary,
    ErrorCategory,
    MessageStatus,
    PersistedTurn,
    StoredMessage,
    safe_error_display,
    to_hex,
    to_uuid,
)
from jarvis.conversations.repository import ConversationRepository
from jarvis.conversations.service import ConversationService

__all__ = [
    "ConversationError",
    "ConversationNotFoundError",
    "ConversationRepository",
    "ConversationService",
    "ConversationSummary",
    "ErrorCategory",
    "InvalidIdentifierError",
    "MessageStatus",
    "PersistedTurn",
    "PersistenceError",
    "PersistenceTimeoutError",
    "PersistenceUnconfirmedError",
    "StoredMessage",
    "safe_error_display",
    "to_hex",
    "to_uuid",
]
