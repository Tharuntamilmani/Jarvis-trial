"""Unit tests for ConversationRepository validation, atomic finalize, and pointers."""

from __future__ import annotations

import contextlib
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from jarvis.conversations.errors import (
    ConversationError,
    ConversationNotFoundError,
)
from jarvis.conversations.models import MessageStatus, PersistedTurn
from jarvis.conversations.repository import ConversationRepository

CONV_A = "a" * 32
CONV_B = "b" * 32
REQ_1 = "1" * 32
REQ_2 = "2" * 32
MSG_1 = "3" * 32


class _FakeResult:
    def __init__(self, *, scalar=None, scalars=None, one=None) -> None:
        self._scalar = scalar
        self._scalars = scalars if scalars is not None else []
        self._one = one

    def scalar_one_or_none(self):
        return self._scalar

    def scalar_one(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return list(self._scalars)

    def one_or_none(self):
        return self._one


class _FakeSession:
    def __init__(self, responses: list[_FakeResult | Exception]) -> None:
        self._responses = list(responses)
        self.added: list[object] = []
        self.executed: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def add_all(self, objs: list[object]) -> None:
        self.added.extend(objs)

    async def flush(self) -> None:
        return None

    async def execute(self, stmt):
        self.executed.append(stmt)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeDb:
    def __init__(self, sessions: list[_FakeSession]) -> None:
        self._sessions = list(sessions)

    @contextlib.asynccontextmanager
    async def session(self):
        yield self._sessions.pop(0)


async def test_create_retry_rejects_same_request_id() -> None:
    repo = ConversationRepository(_FakeDb([]))  # type: ignore[arg-type]
    with pytest.raises(ConversationError):
        await repo.create_retry(
            conversation_id=CONV_A,
            request_id=REQ_1,
            retry_of_request_id=REQ_1,
            owner_session_id="f" * 32,
        )


async def test_create_retry_rejects_nonexistent_original_request() -> None:
    session = _FakeSession(
        [
            _FakeResult(scalars=[]),  # _load_turn
            _FakeResult(scalar=uuid.UUID(CONV_A)),  # conversation FOR UPDATE
            _FakeResult(scalars=[]),  # original request lookup
        ]
    )
    repo = ConversationRepository(_FakeDb([session]))  # type: ignore[arg-type]
    with pytest.raises(ConversationNotFoundError, match="does not exist"):
        await repo.create_retry(
            conversation_id=CONV_A,
            request_id=REQ_2,
            retry_of_request_id=REQ_1,
            owner_session_id="f" * 32,
        )


async def test_create_retry_rejects_cross_conversation_reference() -> None:
    session = _FakeSession(
        [
            _FakeResult(scalars=[]),  # _load_turn
            _FakeResult(scalar=uuid.UUID(CONV_A)),  # conversation FOR UPDATE
            _FakeResult(
                scalars=[uuid.UUID(CONV_B), uuid.UUID(CONV_B)]
            ),  # user + assistant rows in CONV_B
        ]
    )
    repo = ConversationRepository(_FakeDb([session]))  # type: ignore[arg-type]
    with pytest.raises(ConversationNotFoundError, match="different conversation"):
        await repo.create_retry(
            conversation_id=CONV_A,
            request_id=REQ_2,
            retry_of_request_id=REQ_1,
            owner_session_id="f" * 32,
        )


async def test_create_retry_accepts_two_row_original_in_same_conversation() -> None:
    session = _FakeSession(
        [
            _FakeResult(scalars=[]),  # _load_turn
            _FakeResult(scalar=uuid.UUID(CONV_A)),  # conversation FOR UPDATE
            _FakeResult(
                scalars=[uuid.UUID(CONV_A), uuid.UUID(CONV_A)]
            ),  # 2 rows (user + assistant) in CONV_A
            _FakeResult(scalar=2),  # _next_seq max -> +1 = 3
            _FakeResult(),  # _active_setting_stmt
            _FakeResult(),  # update Conversation.updated_at
        ]
    )
    repo = ConversationRepository(_FakeDb([session]))  # type: ignore[arg-type]
    turn = await repo.create_retry(
        conversation_id=CONV_A,
        request_id=REQ_2,
        retry_of_request_id=REQ_1,
        owner_session_id="f" * 32,
    )
    assert isinstance(turn, PersistedTurn)
    assert turn.user_message_id is None
    assert turn.assistant_seq == 3
    assert turn.retry_of_request_id == REQ_1


async def test_finalize_atomic_update_paths() -> None:
    # 1. Open row updated atomically -> returns True
    s_win = _FakeSession([_FakeResult(scalar="completed")])
    repo_win = ConversationRepository(_FakeDb([s_win]))  # type: ignore[arg-type]
    assert (
        await repo_win.finalize(
            MSG_1, status="completed", content="ok", expected_version=0
        )
        is True
    )
    assert len(s_win.executed) == 1  # single atomic UPDATE, no pre-read

    # 2. Already in same terminal status -> idempotent True
    s_idem = _FakeSession(
        [
            _FakeResult(scalar=None),
            _FakeResult(one=SimpleNamespace(status="completed", content_version=1)),
        ]
    )
    repo_idem = ConversationRepository(_FakeDb([s_idem]))  # type: ignore[arg-type]
    assert (
        await repo_idem.finalize(
            MSG_1, status="completed", content="ok", expected_version=0
        )
        is True
    )

    # 3. Already in different terminal status -> returns False
    s_diff = _FakeSession(
        [
            _FakeResult(scalar=None),
            _FakeResult(one=SimpleNamespace(status="completed", content_version=1)),
        ]
    )
    repo_diff = ConversationRepository(_FakeDb([s_diff]))  # type: ignore[arg-type]
    assert (
        await repo_diff.finalize(
            MSG_1, status="failed", content="no", expected_version=0
        )
        is False
    )


async def test_set_active_conversation_rejects_unknown_conversation() -> None:
    session = _FakeSession([_FakeResult(scalar=None)])
    repo = ConversationRepository(_FakeDb([session]))  # type: ignore[arg-type]
    with pytest.raises(ConversationNotFoundError):
        await repo.set_active_conversation(CONV_A)


async def test_get_active_conversation_id_returns_none_for_dangling_pointer() -> None:
    session = _FakeSession(
        [
            _FakeResult(scalar={"id": CONV_A}),  # app_settings row
            _FakeResult(scalar=None),  # conversation existence check fails
        ]
    )
    repo = ConversationRepository(_FakeDb([session]))  # type: ignore[arg-type]
    assert await repo.get_active_conversation_id() is None


async def test_create_turn_reconciles_on_integrity_error() -> None:
    existing_rows = [
        SimpleNamespace(
            id=uuid.UUID("4" * 32),
            conversation_id=uuid.UUID(CONV_A),
            role="user",
            content="hi",
            seq=1,
            request_id=uuid.UUID(REQ_1),
            status="completed",
            content_version=0,
            retry_of_request_id=None,
        ),
        SimpleNamespace(
            id=uuid.UUID(MSG_1),
            conversation_id=uuid.UUID(CONV_A),
            role="assistant",
            content="",
            seq=2,
            request_id=uuid.UUID(REQ_1),
            status=MessageStatus.PENDING.value,
            content_version=0,
            retry_of_request_id=None,
        ),
    ]
    s_conflict = _FakeSession(
        [
            _FakeResult(scalars=[]),  # initial _load_turn
            _FakeResult(scalar=0),  # _next_seq
            IntegrityError("dup", {}, Exception("uq_messages_request_role")),
        ]
    )
    s_lookup = _FakeSession([_FakeResult(scalars=existing_rows)])
    repo = ConversationRepository(_FakeDb([s_conflict, s_lookup]))  # type: ignore[arg-type]

    turn = await repo.create_turn(
        request_id=REQ_1, user_content="hi", owner_session_id="f" * 32
    )
    assert turn.reconciled is True
    assert turn.conversation_id == CONV_A
    assert turn.assistant_message_id == MSG_1


@pytest.mark.parametrize("bad_status", ["pending", "streaming", "bogus"])
async def test_finalize_rejects_non_terminal_status(bad_status: str) -> None:
    repo = ConversationRepository(_FakeDb([]))  # type: ignore[arg-type]
    with pytest.raises(ConversationError, match="terminal"):
        await repo.finalize(
            MSG_1, status=bad_status, content="x", expected_version=0
        )


async def test_mark_stale_open_interrupted_returns_rowcount() -> None:
    res = SimpleNamespace(rowcount=3)
    session = _FakeSession([res])  # type: ignore[list-item]
    repo = ConversationRepository(_FakeDb([session]))  # type: ignore[arg-type]
    updated = await repo.mark_stale_open_interrupted(
        current_session_id="f" * 32
    )
    assert updated == 3
    assert len(session.executed) == 1


async def test_rename_conversation_rejects_empty_title() -> None:
    repo = ConversationRepository(_FakeDb([]))  # type: ignore[arg-type]
    with pytest.raises(ConversationError, match="empty"):
        await repo.rename_conversation(CONV_A, "   ")


async def test_delete_conversation_returns_false_when_missing() -> None:
    session = _FakeSession([_FakeResult(scalar=None)])
    repo = ConversationRepository(_FakeDb([session]))  # type: ignore[arg-type]
    assert await repo.delete_conversation(CONV_A) is False
