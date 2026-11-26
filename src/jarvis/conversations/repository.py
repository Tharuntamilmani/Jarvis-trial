"""Database repository for persistent conversations.

Contains no UI logic and no provider logic. Every write that inserts messages
follows the same locking convention:

1. begin a transaction;
2. lock the parent conversation row (``SELECT ... FOR UPDATE``);
3. allocate sequence numbers while holding that lock;
4. insert;
5. commit.

Sequence numbers are the only ordering authority. Timestamps are never used
to order messages.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import delete, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.exc import IntegrityError

from jarvis.conversations.errors import (
    ConversationError,
    ConversationNotFoundError,
)
from jarvis.conversations.models import (
    TERMINAL_STATUSES,
    ConversationSummary,
    ErrorCategory,
    MessageStatus,
    PersistedTurn,
    StoredMessage,
    to_hex,
    to_uuid,
)
from jarvis.database.connection import DatabaseService
from jarvis.database.models import AppSetting, Conversation, Message
from jarvis.monitoring.logging import get_logger

log = get_logger(__name__)

ACTIVE_CONVERSATION_KEY = "ui.active_conversation_id"

_OPEN = (
    MessageStatus.PENDING.value,
    MessageStatus.STREAMING.value,
)


def _active_setting_stmt(conversation_hex: str) -> Any:
    """Upsert the active-conversation pointer.

    ``updated_at`` is set explicitly: SQLAlchemy ``onupdate`` does not apply to
    raw/INSERT..ON CONFLICT statements.
    """
    return (
        pg_insert(AppSetting)
        .values(
            key=ACTIVE_CONVERSATION_KEY,
            value=literal({"id": conversation_hex}, JSONB),
            updated_at=func.now(),
        )
        .on_conflict_do_update(
            index_elements=[AppSetting.key],
            set_={
                "value": literal({"id": conversation_hex}, JSONB),
                "updated_at": func.now(),
            },
        )
    )


class ConversationRepository:
    """Transactional persistence for conversations, turns and checkpoints."""

    def __init__(self, db: DatabaseService) -> None:
        self._db = db

    # -- Turn creation ---------------------------------------------------------

    async def create_turn(
        self,
        *,
        request_id: str,
        user_content: str,
        owner_session_id: str,
        conversation_id: str | None = None,
        mode: str = "workspace",
        title: str = "Untitled",
    ) -> PersistedTurn:
        """Create one logical turn atomically, or reconcile an existing one.

        When ``conversation_id`` is ``None`` the conversation is created in the
        same transaction as its first two messages and the active-conversation
        pointer, so a failure leaves no partial state behind.
        """
        request_uuid = to_uuid(request_id)
        owner_uuid = to_uuid(owner_session_id)

        try:
            async with self._db.session() as session:
                existing = await self._load_turn(session, request_uuid)
                if existing is not None:
                    return existing

                created = False
                if conversation_id is None:
                    conversation_uuid = uuid.uuid4()
                    resolved_title = _derive_title(user_content, title)
                    session.add(
                        Conversation(
                            id=conversation_uuid, title=resolved_title, mode=mode
                        )
                    )
                    await session.flush()
                    created = True
                else:
                    conversation_uuid = to_uuid(conversation_id)
                    locked = await session.execute(
                        select(Conversation.id)
                        .where(Conversation.id == conversation_uuid)
                        .with_for_update()
                    )
                    if locked.scalar_one_or_none() is None:
                        raise ConversationNotFoundError(
                            f"conversation {conversation_id} does not exist"
                        )

                base_seq = await self._next_seq(session, conversation_uuid)
                user_seq = base_seq
                assistant_seq = base_seq + 1

                user_id = uuid.uuid4()
                assistant_id = uuid.uuid4()
                session.add_all(
                    [
                        Message(
                            id=user_id,
                            conversation_id=conversation_uuid,
                            role="user",
                            content=user_content,
                            seq=user_seq,
                            request_id=request_uuid,
                            status=MessageStatus.COMPLETED.value,
                            owner_session_id=owner_uuid,
                            content_version=0,
                        ),
                        Message(
                            id=assistant_id,
                            conversation_id=conversation_uuid,
                            role="assistant",
                            content="",
                            seq=assistant_seq,
                            request_id=request_uuid,
                            status=MessageStatus.PENDING.value,
                            owner_session_id=owner_uuid,
                            content_version=0,
                        ),
                    ]
                )
                # Acquire the active-pointer row lock before stamping updated_at
                # with clock_timestamp() so concurrent first turns have a
                # deterministic ordering matching the active pointer.
                await session.execute(_active_setting_stmt(conversation_uuid.hex))
                await session.execute(
                    update(Conversation)
                    .where(Conversation.id == conversation_uuid)
                    .values(updated_at=func.clock_timestamp())
                )
                await session.flush()
        except IntegrityError:
            existing_turn = await self.get_turn(request_uuid.hex)
            if existing_turn is not None:
                return existing_turn
            raise

        return PersistedTurn(
            conversation_id=conversation_uuid.hex,
            request_id=request_uuid.hex,
            assistant_message_id=assistant_id.hex,
            user_message_id=user_id.hex,
            user_seq=user_seq,
            assistant_seq=assistant_seq,
            assistant_status=MessageStatus.PENDING,
            assistant_content_version=0,
            created_conversation=created,
        )

    async def create_retry(
        self,
        *,
        conversation_id: str,
        request_id: str,
        retry_of_request_id: str,
        owner_session_id: str,
        content: str = "",
    ) -> PersistedTurn:
        """Persist an explicit retry as an assistant-only generation.

        The original user message is never duplicated: a retry links back to
        the original request through ``retry_of_request_id``.

        Validates that the referenced original request exists and belongs to
        the same conversation; rejects cross-conversation and nonexistent
        references.
        """
        request_uuid = to_uuid(request_id)
        original_uuid = to_uuid(retry_of_request_id)
        owner_uuid = to_uuid(owner_session_id)
        conversation_uuid = to_uuid(conversation_id)
        if request_uuid == original_uuid:
            raise ConversationError(
                "retry request_id must be distinct from retry_of_request_id"
            )

        try:
            async with self._db.session() as session:
                existing = await self._load_turn(session, request_uuid)
                if existing is not None:
                    return existing

                locked = await session.execute(
                    select(Conversation.id)
                    .where(Conversation.id == conversation_uuid)
                    .with_for_update()
                )
                if locked.scalar_one_or_none() is None:
                    raise ConversationNotFoundError(
                        f"conversation {conversation_id} does not exist"
                    )

                # Validate that the original request exists and belongs to this
                # conversation. An original turn owns both a user and an
                # assistant row, so use .all() rather than scalar_one_or_none().
                original_convs = (
                    (
                        await session.execute(
                            select(Message.conversation_id).where(
                                Message.request_id == original_uuid
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                if not original_convs:
                    raise ConversationNotFoundError(
                        f"original request {retry_of_request_id} does not exist"
                    )
                if set(original_convs) != {conversation_uuid}:
                    raise ConversationNotFoundError(
                        f"original request {retry_of_request_id} belongs to a "
                        "different conversation"
                    )

                assistant_seq = await self._next_seq(session, conversation_uuid)
                assistant_id = uuid.uuid4()
                session.add(
                    Message(
                        id=assistant_id,
                        conversation_id=conversation_uuid,
                        role="assistant",
                        content=content,
                        seq=assistant_seq,
                        request_id=request_uuid,
                        retry_of_request_id=original_uuid,
                        status=MessageStatus.PENDING.value,
                        owner_session_id=owner_uuid,
                        content_version=0,
                    )
                )
                await session.execute(_active_setting_stmt(conversation_uuid.hex))
                await session.execute(
                    update(Conversation)
                    .where(Conversation.id == conversation_uuid)
                    .values(updated_at=func.clock_timestamp())
                )
                await session.flush()
        except IntegrityError:
            existing_turn = await self.get_turn(request_uuid.hex)
            if existing_turn is not None:
                return existing_turn
            raise

        return PersistedTurn(
            conversation_id=conversation_uuid.hex,
            request_id=request_uuid.hex,
            assistant_message_id=assistant_id.hex,
            user_message_id=None,
            user_seq=None,
            assistant_seq=assistant_seq,
            assistant_status=MessageStatus.PENDING,
            assistant_content_version=0,
            retry_of_request_id=original_uuid.hex,
        )

    async def _next_seq(self, session: Any, conversation_uuid: uuid.UUID) -> int:
        """Allocate the next sequence number while the parent row is locked."""
        current = await session.execute(
            select(func.coalesce(func.max(Message.seq), 0)).where(
                Message.conversation_id == conversation_uuid
            )
        )
        return int(current.scalar_one()) + 1

    async def _load_turn(
        self, session: Any, request_uuid: uuid.UUID
    ) -> PersistedTurn | None:
        rows = (
            (
                await session.execute(
                    select(Message)
                    .where(Message.request_id == request_uuid)
                    .order_by(Message.seq.asc())
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return None
        return _turn_from_rows(rows)

    async def get_turn(self, request_id: str) -> PersistedTurn | None:
        """Look up a persisted turn by its request UUID (reconciliation)."""
        request_uuid = to_uuid(request_id)
        async with self._db.session() as session:
            return await self._load_turn(session, request_uuid)

    # -- Generation transitions ------------------------------------------------

    async def mark_streaming(
        self, message_id: str, *, expected_version: int
    ) -> int | None:
        """Durably record the dispatch-start transition.

        Returns the new content version, or ``None`` when the row is no longer
        ``pending`` (already transitioned or finalized).
        """
        message_uuid = to_uuid(message_id)
        async with self._db.session() as session:
            result = await session.execute(
                update(Message)
                .where(
                    Message.id == message_uuid,
                    Message.status == MessageStatus.PENDING.value,
                    Message.content_version == expected_version,
                )
                .values(
                    status=MessageStatus.STREAMING.value,
                    content_version=Message.content_version + 1,
                    updated_at=func.now(),
                )
                .returning(Message.content_version)
            )
            return result.scalar_one_or_none()

    async def write_checkpoint(
        self,
        message_id: str,
        *,
        content: str,
        expected_version: int,
    ) -> int | None:
        """Conditionally persist streamed content.

        The update only applies while the row is still ``streaming`` and the
        expected version is current, so a stale checkpoint can never overwrite
        newer content or a terminal state.
        """
        message_uuid = to_uuid(message_id)
        async with self._db.session() as session:
            result = await session.execute(
                update(Message)
                .where(
                    Message.id == message_uuid,
                    Message.status == MessageStatus.STREAMING.value,
                    Message.content_version == expected_version,
                )
                .values(
                    content=content,
                    content_version=Message.content_version + 1,
                    updated_at=func.now(),
                )
                .returning(Message.content_version)
            )
            return result.scalar_one_or_none()

    async def finalize(
        self,
        message_id: str,
        *,
        status: str,
        content: str,
        expected_version: int | None = None,
        error: str | None = None,
        tokens: int | None = None,
        provider: str | None = None,
        model: str | None = None,
        privacy: str | None = None,
    ) -> bool:
        """Persist final content and a terminal status.

        Validates that ``status`` is a member of :data:`TERMINAL_STATUSES`.
        Idempotent: when the row is already in the requested terminal state the
        call reports success without writing. A row in a *different* terminal
        state is never overwritten.

        The write is atomic at the database level: the WHERE clause restricts
        the transition to rows still in an open status (``pending`` or
        ``streaming``), so a concurrent checkpoint cannot overwrite a terminal
        row and ``expected_version`` is accepted for caller compatibility
        without blocking finalization after an intervening checkpoint.
        """
        del expected_version  # terminal transition is authoritative over open rows
        try:
            target_status = MessageStatus(status)
        except ValueError as exc:
            raise ConversationError(f"invalid terminal status: {status!r}") from exc
        if target_status not in TERMINAL_STATUSES:
            raise ConversationError(
                f"status {status!r} is not a terminal status"
            )
        message_uuid = to_uuid(message_id)
        async with self._db.session() as session:
            # Atomically transition from any open status to the terminal state.
            # The open-status guard prevents re-opening a closed row or racing
            # with checkpoints (which require status == 'streaming').
            result = await session.execute(
                update(Message)
                .where(
                    Message.id == message_uuid,
                    Message.status.in_(_OPEN),
                )
                .values(
                    content=content,
                    status=target_status.value,
                    error=error,
                    tokens=tokens,
                    provider=provider,
                    model=model,
                    privacy=privacy,
                    content_version=Message.content_version + 1,
                    updated_at=func.now(),
                )
                .returning(Message.status)
            )
            written = result.scalar_one_or_none()
            if written is not None:
                return written == target_status.value

            # No row was updated. Either the row is already terminal or doesn't
            # exist.
            current = await session.execute(
                select(Message.status, Message.content_version).where(
                    Message.id == message_uuid
                )
            )
            row = current.one_or_none()
            if row is None:
                return False
            if row.status == target_status.value:
                return True  # Already in the requested terminal state.
            # Already in a different terminal state; never overwrite.
            return False

    async def mark_stale_open_interrupted(
        self, *, current_session_id: str
    ) -> int:
        """Transition prior-session open rows to ``interrupted`` on startup.

        Any assistant row still in ``pending`` or ``streaming`` owned by a
        different (or null) session is durably demoted to ``interrupted`` so
        reloaded conversations never show stale in-flight generations.
        """
        session_uuid = to_uuid(current_session_id)
        async with self._db.session() as session:
            result = await session.execute(
                update(Message)
                .where(
                    Message.role == "assistant",
                    Message.status.in_(_OPEN),
                    or_(
                        Message.owner_session_id.is_(None),
                        Message.owner_session_id != session_uuid,
                    ),
                )
                .values(
                    status=MessageStatus.INTERRUPTED.value,
                    error=ErrorCategory.INTERRUPTED.value,
                    content_version=Message.content_version + 1,
                    updated_at=func.now(),
                )
            )
            return int(result.rowcount or 0)

    # -- Reads -----------------------------------------------------------------

    async def load_conversation(
        self, conversation_id: str, *, limit: int = 200
    ) -> list[StoredMessage]:
        """Load the most recent ``limit`` messages in ascending ``seq`` order."""
        conversation_uuid = to_uuid(conversation_id)
        async with self._db.session() as session:
            ids = (
                (
                    await session.execute(
                        select(Message.id)
                        .where(Message.conversation_id == conversation_uuid)
                        .order_by(Message.seq.desc())
                        .limit(max(1, limit))
                    )
                )
                .scalars()
                .all()
            )
            if not ids:
                return []
            rows = (
                (
                    await session.execute(
                        select(Message)
                        .where(Message.id.in_(ids))
                        .order_by(Message.seq.asc())
                    )
                )
                .scalars()
                .all()
            )
        return [_stored(row) for row in rows]

    async def get_conversation(
        self, conversation_id: str
    ) -> ConversationSummary | None:
        conversation_uuid = to_uuid(conversation_id)
        async with self._db.session() as session:
            row = await session.execute(
                select(Conversation).where(Conversation.id == conversation_uuid)
            )
            conversation = row.scalar_one_or_none()
            if conversation is None:
                return None
            count = await session.execute(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == conversation_uuid)
            )
            return _summary(conversation, int(count.scalar_one()))

    async def list_conversations(
        self, *, limit: int = 100
    ) -> list[ConversationSummary]:
        counts = (
            select(
                Message.conversation_id.label("cid"),
                func.count().label("total"),
            )
            .group_by(Message.conversation_id)
            .subquery()
        )
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    select(Conversation, func.coalesce(counts.c.total, 0))
                    .outerjoin(counts, counts.c.cid == Conversation.id)
                    .order_by(
                        Conversation.updated_at.desc(),
                        Conversation.id.desc(),
                    )
                    .limit(max(1, limit))
                )
            ).all()
        return [_summary(row[0], int(row[1])) for row in rows]

    async def rename_conversation(
        self, conversation_id: str, title: str
    ) -> ConversationSummary:
        """Update a conversation's title and return its updated summary."""
        cleaned = " ".join(title.split())[:200]
        if not cleaned:
            raise ConversationError("conversation title cannot be empty")
        conversation_uuid = to_uuid(conversation_id)
        async with self._db.session() as session:
            row = await session.execute(
                update(Conversation)
                .where(Conversation.id == conversation_uuid)
                .values(title=cleaned, updated_at=func.clock_timestamp())
                .returning(Conversation)
            )
            conversation = row.scalar_one_or_none()
            if conversation is None:
                raise ConversationNotFoundError(
                    f"conversation {conversation_id} does not exist"
                )
            count = await session.execute(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == conversation_uuid)
            )
            return _summary(conversation, int(count.scalar_one()))

    async def delete_conversation(self, conversation_id: str) -> bool:
        """Delete a conversation (cascading messages) and clear active pointer."""
        conversation_uuid = to_uuid(conversation_id)
        async with self._db.session() as session:
            res = await session.execute(
                delete(Conversation)
                .where(Conversation.id == conversation_uuid)
                .returning(Conversation.id)
            )
            deleted = res.scalar_one_or_none() is not None
            if not deleted:
                return False
            # Clear active-conversation pointer if it referenced this conversation.
            row = await session.execute(
                select(AppSetting.value).where(
                    AppSetting.key == ACTIVE_CONVERSATION_KEY
                )
            )
            val = row.scalar_one_or_none()
            if isinstance(val, dict) and val.get("id") == conversation_uuid.hex:
                await session.execute(
                    delete(AppSetting).where(
                        AppSetting.key == ACTIVE_CONVERSATION_KEY
                    )
                )
            return True

    # -- Active conversation pointer ------------------------------------------

    async def set_active_conversation(self, conversation_id: str) -> None:
        conversation_uuid = to_uuid(conversation_id)
        async with self._db.session() as session:
            exists = await session.execute(
                select(Conversation.id).where(Conversation.id == conversation_uuid)
            )
            if exists.scalar_one_or_none() is None:
                raise ConversationNotFoundError(
                    f"conversation {conversation_id} does not exist"
                )
            await session.execute(_active_setting_stmt(conversation_uuid.hex))

    async def get_active_conversation_id(self) -> str | None:
        async with self._db.session() as session:
            row = await session.execute(
                select(AppSetting.value).where(
                    AppSetting.key == ACTIVE_CONVERSATION_KEY
                )
            )
            value = row.scalar_one_or_none()
            if not value:
                return None
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    return None
            if not isinstance(value, dict):
                return None
            identifier = value.get("id")
            if not isinstance(identifier, str) or not identifier.strip():
                return None
            try:
                conversation_uuid = to_uuid(identifier.strip())
            except ConversationError:
                return None
            exists = await session.execute(
                select(Conversation.id).where(Conversation.id == conversation_uuid)
            )
            if exists.scalar_one_or_none() is None:
                return None
            return conversation_uuid.hex


def _turn_from_rows(rows: list[Message]) -> PersistedTurn:
    user_row = next((row for row in rows if row.role == "user"), None)
    assistant_row = next(
        (row for row in reversed(rows) if row.role == "assistant"), None
    )
    if assistant_row is None:
        raise ConversationNotFoundError("request has no assistant message row")
    return PersistedTurn(
        conversation_id=assistant_row.conversation_id.hex,
        request_id=to_hex(assistant_row.request_id) or "",
        assistant_message_id=assistant_row.id.hex,
        user_message_id=user_row.id.hex if user_row is not None else None,
        user_seq=user_row.seq if user_row is not None else None,
        assistant_seq=assistant_row.seq,
        assistant_status=MessageStatus(assistant_row.status),
        assistant_content_version=assistant_row.content_version,
        reconciled=True,
        retry_of_request_id=to_hex(assistant_row.retry_of_request_id),
    )


def _stored(row: Message) -> StoredMessage:
    return StoredMessage(
        id=row.id.hex,
        conversation_id=row.conversation_id.hex,
        role=row.role,
        content=row.content,
        seq=row.seq,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
        request_id=to_hex(row.request_id),
        error=row.error,
        provider=row.provider,
        model=row.model,
        privacy=row.privacy,
        tokens=row.tokens,
        retry_of_request_id=to_hex(row.retry_of_request_id),
        content_version=row.content_version,
    )


def _derive_title(user_content: str, fallback: str = "Untitled") -> str:
    """Derive a bounded local title from the first user message."""
    if fallback != "Untitled":
        cleaned_fallback = " ".join(fallback.split())[:200]
        if cleaned_fallback:
            return cleaned_fallback
    first_line = user_content.strip().splitlines()[0] if user_content.strip() else ""
    cleaned = " ".join(first_line.split())
    if not cleaned:
        return "Untitled"
    if len(cleaned) > 60:
        return cleaned[:57].rstrip() + "..."
    return cleaned


def _summary(conversation: Conversation, message_count: int) -> ConversationSummary:
    return ConversationSummary(
        id=conversation.id.hex,
        title=conversation.title,
        mode=conversation.mode,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        message_count=message_count,
    )
