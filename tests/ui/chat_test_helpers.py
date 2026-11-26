"""Test-only in-memory ConversationPersistence stub for UI unit tests.

Production ChatController requires a real ConversationService and fails closed
when persistence is unavailable. UI tests that exercise streaming, consent,
or view rendering without PostgreSQL pass this explicit test stub.
"""

from __future__ import annotations

from datetime import datetime

from jarvis.conversations.models import (
    MessageStatus,
    PersistedTurn,
    StoredMessage,
)


class StubConversationPersistence:
    """Deterministic in-memory persistence stub for non-DB UI tests."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.fail_initial = False
        self.fail_dispatch = False
        self.finalize_result = True
        self.checkpoints: list[tuple[str, str, int]] = []
        self.finals: list[dict] = []
        self.stored_messages: dict[str, list[StoredMessage]] = {}
        self.active_conversation_id: str | None = None
        self.drain_calls = 0
        self._seq = 0

    async def persist_initial_turn(
        self,
        *,
        request_id: str,
        user_content: str,
        conversation_id: str | None = None,
    ) -> PersistedTurn:
        self.events.append("persist")
        if self.fail_initial:
            raise RuntimeError("database unavailable")
        conv_id = conversation_id or ("d" * 32)
        self._seq += 1
        user_seq = self._seq
        self._seq += 1
        assistant_seq = self._seq
        self.active_conversation_id = conv_id
        return PersistedTurn(
            conversation_id=conv_id,
            request_id=request_id,
            assistant_message_id=f"a{assistant_seq:031x}",
            user_message_id=f"u{user_seq:031x}",
            user_seq=user_seq,
            assistant_seq=assistant_seq,
            assistant_status=MessageStatus.PENDING,
            assistant_content_version=0,
            created_conversation=conversation_id is None,
        )

    async def mark_streaming(self, turn: PersistedTurn) -> int:
        self.events.append("dispatch")
        if self.fail_dispatch:
            raise RuntimeError("dispatch state rejected")
        return turn.assistant_content_version + 1

    async def write_checkpoint(
        self, message_id: str, content: str, expected_version: int
    ) -> int | None:
        self.events.append("checkpoint")
        self.checkpoints.append((message_id, content, expected_version))
        return expected_version + 1

    async def finalize(
        self,
        message_id: str,
        *,
        status: str,
        content: str,
        expected_version: int,
        error: str | None = None,
        tokens: int = 0,
        provider: str | None = None,
        model: str | None = None,
        privacy: str | None = None,
    ) -> bool:
        self.events.append("finalize")
        self.finals.append(
            {
                "message_id": message_id,
                "status": status,
                "content": content,
                "error": error,
                "version": expected_version,
                "tokens": tokens,
                "provider": provider,
                "model": model,
                "privacy": privacy,
            }
        )
        return self.finalize_result

    async def load_conversation(
        self, conversation_id: str, *, limit: int = 200
    ) -> list[StoredMessage]:
        return list(self.stored_messages.get(conversation_id, []))[-limit:]

    async def get_active_conversation_id(self) -> str | None:
        return self.active_conversation_id

    async def drain_reconcile(self) -> None:
        self.drain_calls += 1

    async def reconcile_stale_generations(self) -> int:
        return 0


def make_stored_message(
    *,
    id: str = "1" * 32,
    conversation_id: str = "d" * 32,
    role: str = "assistant",
    content: str = "",
    seq: int = 1,
    status: str = "completed",
    error: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    privacy: str | None = None,
    tokens: int | None = None,
    request_id: str | None = None,
) -> StoredMessage:
    now = datetime.now()
    return StoredMessage(
        id=id,
        conversation_id=conversation_id,
        role=role,
        content=content,
        seq=seq,
        status=status,
        created_at=now,
        updated_at=now,
        request_id=request_id,
        error=error,
        provider=provider,
        model=model,
        privacy=privacy,
        tokens=tokens,
    )
