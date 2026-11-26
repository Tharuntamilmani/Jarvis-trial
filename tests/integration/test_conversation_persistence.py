"""Conversation persistence against the dedicated jarvis_test database."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from jarvis.conversations.errors import (
    ConversationError,
    ConversationNotFoundError,
    InvalidIdentifierError,
)

REQUEST = "1" * 32
SECOND = "2" * 32
RETRY = "3" * 32


async def test_first_turn_is_atomic(conversations) -> None:
    turn = await conversations.persist_initial_turn(
        request_id=REQUEST, user_content="hello"
    )

    assert turn.created_conversation is True
    assert (turn.user_seq, turn.assistant_seq) == (1, 2)

    messages = await conversations.load_conversation(turn.conversation_id)
    assert [(m.seq, m.role, m.status) for m in messages] == [
        (1, "user", "completed"),
        (2, "assistant", "pending"),
    ]
    assert messages[0].request_id == REQUEST
    assert messages[1].request_id == REQUEST
    assert await conversations.get_active_conversation_id() == turn.conversation_id


async def test_duplicate_request_id_is_reconciled(conversations) -> None:
    first = await conversations.persist_initial_turn(
        request_id=REQUEST, user_content="hello"
    )
    second = await conversations.persist_initial_turn(
        request_id=REQUEST, user_content="hello"
    )

    assert second.reconciled is True
    assert second.assistant_message_id == first.assistant_message_id

    messages = await conversations.load_conversation(first.conversation_id)
    assert len(messages) == 2


async def test_second_turn_gets_consecutive_sequence(conversations) -> None:
    first = await conversations.persist_initial_turn(
        request_id=REQUEST, user_content="one"
    )
    second = await conversations.persist_initial_turn(
        request_id=SECOND,
        user_content="two",
        conversation_id=first.conversation_id,
    )

    assert second.created_conversation is False
    assert (second.user_seq, second.assistant_seq) == (3, 4)

    messages = await conversations.load_conversation(first.conversation_id)
    assert [m.seq for m in messages] == [1, 2, 3, 4]


async def test_invalid_request_id_is_rejected(conversations) -> None:
    with pytest.raises(InvalidIdentifierError):
        await conversations.persist_initial_turn(
            request_id="not-a-uuid", user_content="x"
        )


async def test_unknown_conversation_leaves_no_rows(conversations) -> None:
    with pytest.raises(ConversationNotFoundError):
        await conversations.persist_initial_turn(
            request_id=REQUEST,
            user_content="x",
            conversation_id="9" * 32,
        )

    assert await conversations.list_conversations() == []


async def test_retry_is_assistant_only_and_linked(conversations) -> None:
    first = await conversations.persist_initial_turn(
        request_id=REQUEST, user_content="question"
    )
    retry = await conversations.persist_retry(
        conversation_id=first.conversation_id,
        request_id=RETRY,
        retry_of_request_id=REQUEST,
    )

    assert retry.user_message_id is None
    assert retry.assistant_seq == 3
    assert retry.retry_of_request_id == REQUEST

    messages = await conversations.load_conversation(first.conversation_id)
    assert [(m.seq, m.role) for m in messages] == [
        (1, "user"),
        (2, "assistant"),
        (3, "assistant"),
    ]
    assert messages[2].retry_of_request_id == REQUEST
    assert messages[0].content == "question"


async def test_duplicate_retry_is_idempotent(conversations) -> None:
    first = await conversations.persist_initial_turn(
        request_id=REQUEST, user_content="q"
    )
    one = await conversations.persist_retry(
        conversation_id=first.conversation_id,
        request_id=RETRY,
        retry_of_request_id=REQUEST,
    )
    two = await conversations.persist_retry(
        conversation_id=first.conversation_id,
        request_id=RETRY,
        retry_of_request_id=REQUEST,
    )

    assert two.assistant_message_id == one.assistant_message_id
    messages = await conversations.load_conversation(first.conversation_id)
    assert len(messages) == 3


async def test_checkpoint_round_trip_and_stale_rejection(
    conversations,
) -> None:
    turn = await conversations.persist_initial_turn(
        request_id=REQUEST, user_content="hi"
    )
    version = await conversations.mark_streaming(turn)
    written = await conversations.write_checkpoint(
        turn.assistant_message_id, "abc", version
    )
    assert written == version + 1

    stale = await conversations.write_checkpoint(
        turn.assistant_message_id, "STALE", version
    )
    assert stale is None

    messages = await conversations.load_conversation(turn.conversation_id)
    assert messages[1].content == "abc"
    assert messages[1].status == "streaming"


async def test_no_write_is_accepted_after_terminal(conversations) -> None:
    turn = await conversations.persist_initial_turn(
        request_id=REQUEST, user_content="hi"
    )
    version = await conversations.mark_streaming(turn)
    confirmed = await conversations.finalize(
        turn.assistant_message_id,
        status="completed",
        content="final",
        expected_version=version,
        tokens=5,
        provider="ollama",
        model="qwen3:8b",
        privacy="local",
    )
    assert confirmed is True

    assert (
        await conversations.write_checkpoint(turn.assistant_message_id, "LATE", version)
        is None
    )
    # Idempotent terminal confirmation.
    assert (
        await conversations.finalize(
            turn.assistant_message_id,
            status="completed",
            content="final",
            expected_version=version,
        )
        is True
    )
    # A different terminal state is never allowed to overwrite.
    assert (
        await conversations.finalize(
            turn.assistant_message_id,
            status="failed",
            content="other",
            expected_version=version,
        )
        is False
    )

    messages = await conversations.load_conversation(turn.conversation_id)
    assert messages[1].status == "completed"
    assert messages[1].content == "final"
    assert messages[1].provider == "ollama"


async def test_concurrent_turns_allocate_distinct_sequences(
    conversations,
) -> None:
    seed = await conversations.persist_initial_turn(
        request_id=REQUEST, user_content="seed"
    )
    left, right = "4" * 32, "5" * 32

    results = await asyncio.gather(
        conversations.persist_initial_turn(
            request_id=left,
            user_content="a",
            conversation_id=seed.conversation_id,
        ),
        conversations.persist_initial_turn(
            request_id=right,
            user_content="b",
            conversation_id=seed.conversation_id,
        ),
    )

    allocated = sorted(
        seq for turn in results for seq in (turn.user_seq, turn.assistant_seq)
    )
    assert allocated == [3, 4, 5, 6]

    messages = await conversations.load_conversation(seed.conversation_id)
    assert [m.seq for m in messages] == [1, 2, 3, 4, 5, 6]
    assert len({m.seq for m in messages}) == 6


async def test_active_pointer_tracks_latest_turn(conversations) -> None:
    first = await conversations.persist_initial_turn(
        request_id=REQUEST, user_content="one"
    )
    await conversations.persist_initial_turn(
        request_id=SECOND,
        user_content="two",
        conversation_id=first.conversation_id,
    )

    assert await conversations.get_active_conversation_id() == first.conversation_id

    listed = await conversations.list_conversations()
    assert listed[0].id == first.conversation_id
    assert listed[0].message_count == 4


# -- Finding 4: Retry integrity ----------------------------------------------


async def test_retry_rejects_nonexistent_original_request(conversations) -> None:
    first = await conversations.persist_initial_turn(
        request_id=REQUEST, user_content="question"
    )
    with pytest.raises(ConversationNotFoundError):
        await conversations.persist_retry(
            conversation_id=first.conversation_id,
            request_id=RETRY,
            retry_of_request_id="9" * 32,
        )


async def test_retry_rejects_cross_conversation_reference(conversations) -> None:
    conv_a = await conversations.persist_initial_turn(
        request_id=REQUEST, user_content="question a"
    )
    conv_b = await conversations.persist_initial_turn(
        request_id=SECOND, user_content="question b"
    )
    with pytest.raises(ConversationNotFoundError):
        await conversations.persist_retry(
            conversation_id=conv_b.conversation_id,
            request_id=RETRY,
            retry_of_request_id=conv_a.request_id,
        )


async def test_retry_rejects_same_request_id(conversations) -> None:
    first = await conversations.persist_initial_turn(
        request_id=REQUEST, user_content="question"
    )
    with pytest.raises(ConversationError):
        await conversations.persist_retry(
            conversation_id=first.conversation_id,
            request_id=REQUEST,
            retry_of_request_id=REQUEST,
        )


async def test_retry_normalizes_dashed_uuids(conversations) -> None:
    first = await conversations.persist_initial_turn(
        request_id=str(uuid.UUID(REQUEST)), user_content="question"
    )
    retry = await conversations.persist_retry(
        conversation_id=str(uuid.UUID(first.conversation_id)),
        request_id=str(uuid.UUID(RETRY)),
        retry_of_request_id=str(uuid.UUID(REQUEST)),
    )
    assert retry.conversation_id == first.conversation_id
    assert retry.request_id == RETRY
    assert retry.retry_of_request_id == REQUEST


# -- Finding 5: Database finalization race -----------------------------------


async def test_concurrent_finalize_and_checkpoint_preserve_terminal_truth(
    conversations,
) -> None:
    turn = await conversations.persist_initial_turn(
        request_id=REQUEST, user_content="hi"
    )
    version = await conversations.mark_streaming(turn)

    checkpoint_res, finalize_res = await asyncio.gather(
        conversations.write_checkpoint(
            turn.assistant_message_id, "partial checkpoint", version
        ),
        conversations.finalize(
            turn.assistant_message_id,
            status="completed",
            content="authoritative final",
            expected_version=version,
            tokens=7,
            provider="ollama",
            model="qwen3:8b",
            privacy="local",
        ),
    )

    assert finalize_res is True
    # Regardless of which committed first, terminal state must hold final text
    # and subsequent checkpoints must be rejected.
    assert (
        await conversations.write_checkpoint(
            turn.assistant_message_id, "late overwrite", version + 1
        )
        is None
    )
    messages = await conversations.load_conversation(turn.conversation_id)
    assert messages[1].status == "completed"
    assert messages[1].content == "authoritative final"
    assert checkpoint_res is None or isinstance(checkpoint_res, int)


async def test_concurrent_finalize_only_allows_one_terminal_transition(
    conversations,
) -> None:
    turn = await conversations.persist_initial_turn(
        request_id=REQUEST, user_content="hi"
    )
    version = await conversations.mark_streaming(turn)

    win_a, win_b = await asyncio.gather(
        conversations.finalize(
            turn.assistant_message_id,
            status="completed",
            content="completed text",
            expected_version=version,
        ),
        conversations.finalize(
            turn.assistant_message_id,
            status="failed",
            content="failed text",
            expected_version=version,
            error="provider_error",
        ),
    )

    assert sorted([win_a, win_b]) == [False, True]
    messages = await conversations.load_conversation(turn.conversation_id)
    if win_a:
        assert messages[1].status == "completed"
        assert messages[1].content == "completed text"
    else:
        assert messages[1].status == "failed"
        assert messages[1].content == "failed text"


# -- Finding 6: Active conversation pointer ----------------------------------


async def test_concurrent_first_turn_creation_keeps_active_pointer_consistent(
    conversations,
) -> None:
    left, right = await asyncio.gather(
        conversations.persist_initial_turn(request_id=REQUEST, user_content="conv one"),
        conversations.persist_initial_turn(request_id=SECOND, user_content="conv two"),
    )

    assert left.conversation_id != right.conversation_id
    assert left.created_conversation is True
    assert right.created_conversation is True

    active = await conversations.get_active_conversation_id()
    listed = await conversations.list_conversations()
    assert len(listed) == 2
    assert active in {left.conversation_id, right.conversation_id}
    assert active == listed[0].id


async def test_set_active_conversation_rejects_unknown_conversation(
    conversations,
) -> None:
    with pytest.raises(ConversationNotFoundError):
        await conversations.set_active_conversation("9" * 32)


async def test_get_active_conversation_id_ignores_dangling_pointer(
    conversations, db_service
) -> None:
    from sqlalchemy import text

    async with db_service.session() as session:
        await session.execute(
            text(
                "INSERT INTO app_settings (key, value) "
                "VALUES ('ui.active_conversation_id', "
                '\'{"id": "99999999999999999999999999999999"}\'::jsonb) '
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            )
        )
    assert await conversations.get_active_conversation_id() is None


async def test_reconcile_stale_generations_demotes_prior_session_only(
    conversations, db_service, db_settings
) -> None:
    from jarvis.conversations.service import ConversationService

    prior = ConversationService.from_database(
        db_service, db_settings, session_id="b" * 32
    )
    stale_turn = await prior.persist_initial_turn(
        request_id=REQUEST, user_content="crashed"
    )
    await prior.mark_streaming(stale_turn)

    current_turn = await conversations.persist_initial_turn(
        request_id=SECOND, user_content="live"
    )

    demoted = await conversations.reconcile_stale_generations()
    assert demoted == 1

    stale_msgs = await conversations.load_conversation(stale_turn.conversation_id)
    assert stale_msgs[1].status == "interrupted"
    assert stale_msgs[1].error == "interrupted"

    live_msgs = await conversations.load_conversation(current_turn.conversation_id)
    assert live_msgs[1].status == "pending"


async def test_rename_and_delete_conversation_clears_active_pointer(
    conversations,
) -> None:
    turn = await conversations.persist_initial_turn(
        request_id=REQUEST, user_content="How do black holes evaporate?"
    )
    listed = await conversations.list_conversations()
    assert listed[0].title == "How do black holes evaporate?"

    renamed = await conversations.rename_conversation(
        turn.conversation_id, "Hawking Radiation"
    )
    assert renamed.title == "Hawking Radiation"
    assert await conversations.get_active_conversation_id() == turn.conversation_id

    assert await conversations.delete_conversation(turn.conversation_id) is True
    assert await conversations.list_conversations() == []
    assert await conversations.get_active_conversation_id() is None
    assert await conversations.delete_conversation(turn.conversation_id) is False
