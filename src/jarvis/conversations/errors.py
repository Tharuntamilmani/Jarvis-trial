"""Errors raised by the persistent conversation subsystem."""

from __future__ import annotations

from jarvis.core.exceptions import JarvisError


class ConversationError(JarvisError):
    """Base error for conversation persistence failures."""


class ConversationNotFoundError(ConversationError):
    """The requested conversation does not exist."""


class InvalidIdentifierError(ConversationError):
    """A request or conversation identifier is not a valid UUID."""


class PersistenceError(ConversationError):
    """A database operation could not be completed."""


class PersistenceTimeoutError(PersistenceError):
    """The persistence deadline expired before the write was confirmed."""


class PersistenceUnconfirmedError(PersistenceError):
    """The outcome of a persistence operation could not be determined.

    The provider must NOT be dispatched: the turn may or may not exist.
    """


__all__ = [
    "ConversationError",
    "ConversationNotFoundError",
    "InvalidIdentifierError",
    "PersistenceError",
    "PersistenceTimeoutError",
    "PersistenceUnconfirmedError",
]
