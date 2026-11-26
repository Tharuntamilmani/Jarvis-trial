"""Tier 3: Conversation memory adapter (local-only)."""

from __future__ import annotations

from jarvis.conversations.models import StoredMessage
from jarvis.conversations.service import ConversationService
from jarvis.memory.short_term import ShortTermMemory


class ConversationMemoryAdapter:
    """Synchronizes persisted conversation messages into short-term memory."""

    def __init__(
        self,
        conversations: ConversationService,
        short_term: ShortTermMemory,
    ) -> None:
        self._conversations = conversations
        self._short_term = short_term

    async def hydrate_from_conversation(
        self, conversation_id: str, *, limit: int = 20
    ) -> list[StoredMessage]:
        """Load recent persisted messages into the short-term ring buffer."""
        messages = await self._conversations.load_conversation(
            conversation_id, limit=limit
        )
        self._short_term.clear()
        for msg in messages:
            if msg.status == "completed" and msg.content.strip():
                self._short_term.append(msg.role, msg.content)
        return messages
