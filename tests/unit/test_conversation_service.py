"""ConversationService: deadlines, reconciliation and failure taxonomy."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jarvis.config import Settings
from jarvis.conversations.errors import (
    PersistenceError,
    PersistenceTimeoutError,
)
from jarvis.conversations.models import (
    ErrorCategory,
    PersistedTurn,
    safe_error_display,
)
from jarvis.conversations.service import ConversationService

REQUEST = "b" * 32


def _turn(request_id: str = REQUEST) -> PersistedTurn:
    return PersistedTurn(
        conversation_id="c" * 32,
        request_id=request_id,
        assistant_message_id="d" * 32,
        user_message_id="e" * 32,
        user_seq=1,
        assistant_seq=2,
    )


class FakeRepository:
    def __init__(self) -> None:
        self.turns: dict[str, PersistedTurn] = {}
        self.create_delay = 0.0
        self.create_error: BaseException | None = None
        self.streaming_result: int | None = 1
        self.streaming_error: BaseException | None = None
        self.get_turn_calls: list[str] = []

    async def create_turn(self, **kwargs):
        if self.create_delay:
            await asyncio.sleep(self.create_delay)
        if self.create_error is not None:
            raise self.create_error
        return self.turns[kwargs["request_id"]]

    async def create_retry(self, **kwargs):
        if self.create_delay:
            await asyncio.sleep(self.create_delay)
        if self.create_error is not None:
            raise self.create_error
        return self.turns[kwargs["request_id"]]

    async def get_turn(self, request_id: str):
        self.get_turn_calls.append(request_id)
        return self.turns.get(request_id)

    async def mark_streaming(self, message_id: str, *, expected_version: int):
        if self.streaming_error is not None:
            raise self.streaming_error
        return self.streaming_result


def _settings(tmp_path: Path, deadline: float = 0.05) -> Settings:
    return Settings(
        log_dir=tmp_path / "logs",
        db_persistence_deadline_seconds=deadline,
        _env_file=None,  # type: ignore[call-arg]
    )


def _service(tmp_path: Path, repo: FakeRepository, deadline: float = 0.05):
    return ConversationService(
        repo,  # type: ignore[arg-type]
        _settings(tmp_path, deadline),
        session_id="a" * 32,
    )


async def test_successful_turn_is_returned(tmp_path: Path) -> None:
    repo = FakeRepository()
    repo.turns[REQUEST] = _turn()
    service = _service(tmp_path, repo)

    result = await service.persist_initial_turn(
        request_id=REQUEST, user_content="hello"
    )

    assert result.assistant_message_id == "d" * 32
    assert repo.get_turn_calls == []


async def test_timeout_reconciles_a_committed_turn(tmp_path: Path) -> None:
    repo = FakeRepository()
    repo.turns[REQUEST] = _turn()
    repo.create_delay = 0.5
    service = _service(tmp_path, repo)

    result = await service.persist_initial_turn(
        request_id=REQUEST, user_content="hello"
    )

    assert result.request_id == REQUEST
    assert repo.get_turn_calls == [REQUEST]


async def test_timeout_without_a_committed_turn_raises(
    tmp_path: Path,
) -> None:
    repo = FakeRepository()
    repo.create_delay = 0.5
    service = _service(tmp_path, repo)

    with pytest.raises(PersistenceTimeoutError):
        await service.persist_initial_turn(request_id=REQUEST, user_content="hello")
    assert repo.get_turn_calls == [REQUEST]


async def test_generic_failure_reconciles_a_committed_turn(
    tmp_path: Path,
) -> None:
    repo = FakeRepository()
    repo.turns[REQUEST] = _turn()
    repo.create_error = RuntimeError("connection reset")
    service = _service(tmp_path, repo)

    result = await service.persist_initial_turn(
        request_id=REQUEST, user_content="hello"
    )

    assert result.request_id == REQUEST


async def test_generic_failure_without_a_turn_raises_persistence_error(
    tmp_path: Path,
) -> None:
    repo = FakeRepository()
    repo.create_error = RuntimeError("connection reset")
    service = _service(tmp_path, repo)

    with pytest.raises(PersistenceError):
        await service.persist_initial_turn(request_id=REQUEST, user_content="hello")


async def test_reconciliation_failure_is_not_mistaken_for_absence(
    tmp_path: Path,
) -> None:
    repo = FakeRepository()
    repo.create_error = RuntimeError("down")

    async def broken_lookup(request_id: str):
        raise RuntimeError("still down")

    repo.get_turn = broken_lookup  # type: ignore[assignment]
    service = _service(tmp_path, repo)

    with pytest.raises(PersistenceError):
        await service.persist_initial_turn(request_id=REQUEST, user_content="hello")


async def test_mark_streaming_rejection_raises(tmp_path: Path) -> None:
    repo = FakeRepository()
    repo.streaming_result = None
    service = _service(tmp_path, repo)

    with pytest.raises(PersistenceError):
        await service.mark_streaming(_turn())


async def test_mark_streaming_timeout_raises(tmp_path: Path) -> None:
    repo = FakeRepository()

    async def slow(message_id: str, *, expected_version: int):
        await asyncio.sleep(0.5)
        return 1

    repo.mark_streaming = slow  # type: ignore[assignment]
    service = _service(tmp_path, repo)

    with pytest.raises(PersistenceTimeoutError):
        await service.mark_streaming(_turn())


async def test_cancellation_propagates_and_schedules_reconcile(
    tmp_path: Path,
) -> None:
    repo = FakeRepository()
    repo.create_delay = 0.5
    service = _service(tmp_path, repo, deadline=5.0)

    task = asyncio.create_task(
        service.persist_initial_turn(request_id=REQUEST, user_content="hello")
    )
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Best-effort reconciliation runs in the background.
    for _ in range(20):
        if repo.get_turn_calls:
            break
        await asyncio.sleep(0.01)
    assert repo.get_turn_calls == [REQUEST]


async def test_retry_timeout_reconciles(tmp_path: Path) -> None:
    repo = FakeRepository()
    repo.turns[REQUEST] = _turn()
    repo.create_delay = 0.5
    service = _service(tmp_path, repo)

    result = await service.persist_retry(
        conversation_id="c" * 32,
        request_id=REQUEST,
        retry_of_request_id="f" * 32,
    )

    assert result.request_id == REQUEST


async def test_close_drains_reconciliation_tasks(tmp_path: Path) -> None:
    repo = FakeRepository()
    gate = asyncio.Event()
    started = asyncio.Event()

    async def slow_lookup(request_id: str):
        started.set()
        await gate.wait()
        repo.get_turn_calls.append(request_id)
        return None

    repo.create_delay = 0.5
    repo.get_turn = slow_lookup  # type: ignore[assignment]
    service = _service(tmp_path, repo, deadline=5.0)

    task = asyncio.create_task(
        service.persist_initial_turn(request_id=REQUEST, user_content="hello")
    )
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await started.wait()
    gate.set()
    await service.close()
    assert repo.get_turn_calls == [REQUEST]
    assert service._reconcile_tasks == set()  # noqa: SLF001


@pytest.mark.parametrize("category", list(ErrorCategory))
def test_safe_error_display_covers_all_categories(category: ErrorCategory) -> None:
    text = safe_error_display(category.value)
    assert isinstance(text, str) and len(text) > 0


def test_safe_error_display_does_not_echo_unknown_text() -> None:
    assert safe_error_display(None) == ""
    assert safe_error_display("") == ""
    unknown = safe_error_display("secret raw provider stack trace")
    assert unknown == safe_error_display(ErrorCategory.UNKNOWN.value)
    assert "secret" not in unknown


@pytest.mark.parametrize(
    ("method", "call"),
    [
        ("write_checkpoint", lambda s: s.write_checkpoint("m" * 32, "hi", 0)),
        (
            "finalize",
            lambda s: s.finalize(
                "m" * 32, status="completed", content="hi", expected_version=0
            ),
        ),
        ("load_conversation", lambda s: s.load_conversation("c" * 32)),
        ("list_conversations", lambda s: s.list_conversations()),
        ("get_conversation", lambda s: s.get_conversation("c" * 32)),
        ("set_active_conversation", lambda s: s.set_active_conversation("c" * 32)),
        ("get_active_conversation_id", lambda s: s.get_active_conversation_id()),
        ("mark_stale_open_interrupted", lambda s: s.reconcile_stale_generations()),
        ("rename_conversation", lambda s: s.rename_conversation("c" * 32, "New")),
        ("delete_conversation", lambda s: s.delete_conversation("c" * 32)),
    ],
)
async def test_service_normalizes_timeout_and_driver_errors(
    tmp_path: Path, method: str, call
) -> None:
    repo = FakeRepository()

    async def slow(*args, **kwargs):
        await asyncio.sleep(0.5)

    setattr(repo, method, slow)
    service = _service(tmp_path, repo, deadline=0.02)
    with pytest.raises(PersistenceTimeoutError):
        await call(service)

    async def broken(*args, **kwargs):
        raise RuntimeError("driver boom")

    setattr(repo, method, broken)
    with pytest.raises(PersistenceError):
        await call(service)

    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()

    setattr(repo, method, cancelled)
    with pytest.raises(asyncio.CancelledError):
        await call(service)
