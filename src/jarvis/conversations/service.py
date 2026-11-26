"""Conversation service: deadlines, reconciliation and durable turn control.

Sits above :class:`ConversationRepository` and adds the application-level
behaviour the repository must not own:

- a bounded persistence deadline per operation;
- ambiguous-outcome reconciliation through the request UUID;
- an explicit failure taxonomy instead of raw driver errors.

It never talks to a provider and never imports Qt.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator

from jarvis.config import Settings
from jarvis.conversations.errors import (
    ConversationError,
    PersistenceError,
    PersistenceTimeoutError,
)
from jarvis.conversations.models import (
    ConversationSummary,
    PersistedTurn,
    StoredMessage,
)
from jarvis.conversations.repository import ConversationRepository
from jarvis.database.connection import DatabaseService
from jarvis.monitoring.logging import get_logger

log = get_logger(__name__)


class ConversationService:
    """Durable conversation operations with bounded latency."""

    def __init__(
        self,
        repository: ConversationRepository,
        settings: Settings,
        *,
        session_id: str | None = None,
    ) -> None:
        self._repo = repository
        self._settings = settings
        self._deadline = float(settings.db_persistence_deadline_seconds)
        self._session_id = session_id or uuid.uuid4().hex
        self._reconcile_tasks: set[asyncio.Task[PersistedTurn | None]] = set()

    @classmethod
    def from_database(
        cls,
        db: DatabaseService,
        settings: Settings,
        *,
        session_id: str | None = None,
    ) -> ConversationService:
        return cls(ConversationRepository(db), settings, session_id=session_id)

    @property
    def session_id(self) -> str:
        """Process ownership marker written to every generation row."""
        return self._session_id

    # -- Turn creation ---------------------------------------------------------

    async def persist_initial_turn(
        self,
        *,
        request_id: str,
        user_content: str,
        conversation_id: str | None = None,
        mode: str = "workspace",
        title: str = "Untitled",
    ) -> PersistedTurn:
        """Persist the first turn before any provider is invoked.

        On timeout or failure the outcome is reconciled through the same
        request UUID in a fresh session. A confirmed commit is returned as
        normal; a confirmed absence raises; an unconfirmed outcome raises
        :class:`PersistenceTimeoutError` so the caller cannot dispatch.
        """
        try:
            async with asyncio.timeout(self._deadline):
                return await self._repo.create_turn(
                    request_id=request_id,
                    user_content=user_content,
                    owner_session_id=self._session_id,
                    conversation_id=conversation_id,
                    mode=mode,
                    title=title,
                )
        except asyncio.CancelledError:
            # A cancellation during commit is ambiguous. Attempt a bounded
            # reconciliation without blocking the cancellation itself.
            self._schedule_reconcile(request_id)
            raise
        except TimeoutError as exc:
            turn = await self._reconcile_quiet(request_id)
            if turn is not None:
                log.info("turn confirmed after persistence timeout")
                return turn
            raise PersistenceTimeoutError(
                "the conversation turn was not persisted within the deadline"
            ) from exc
        except ConversationError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalised below
            turn = await self._reconcile_quiet(request_id)
            if turn is not None:
                log.info("turn confirmed after persistence failure")
                return turn
            raise PersistenceError(
                "the conversation turn could not be persisted"
            ) from exc

    async def persist_retry(
        self,
        *,
        conversation_id: str,
        request_id: str,
        retry_of_request_id: str,
        content: str = "",
    ) -> PersistedTurn:
        """Persist an explicit retry (assistant-only, new request UUID)."""
        try:
            async with asyncio.timeout(self._deadline):
                return await self._repo.create_retry(
                    conversation_id=conversation_id,
                    request_id=request_id,
                    retry_of_request_id=retry_of_request_id,
                    owner_session_id=self._session_id,
                    content=content,
                )
        except asyncio.CancelledError:
            self._schedule_reconcile(request_id)
            raise
        except TimeoutError as exc:
            turn = await self._reconcile_quiet(request_id)
            if turn is not None:
                return turn
            raise PersistenceTimeoutError(
                "the retry was not persisted within the deadline"
            ) from exc
        except ConversationError:
            raise
        except Exception as exc:  # noqa: BLE001
            turn = await self._reconcile_quiet(request_id)
            if turn is not None:
                return turn
            raise PersistenceError("the retry could not be persisted") from exc

    # -- Generation transitions ------------------------------------------------

    async def mark_streaming(self, turn: PersistedTurn) -> int:
        """Durably record dispatch-start; returns the new content version."""
        try:
            async with asyncio.timeout(self._deadline):
                version = await self._repo.mark_streaming(
                    turn.assistant_message_id,
                    expected_version=turn.assistant_content_version,
                )
        except TimeoutError as exc:
            raise PersistenceTimeoutError(
                "the provider dispatch state was not confirmed"
            ) from exc
        except ConversationError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(
                "the provider dispatch state could not be persisted"
            ) from exc
        if version is None:
            raise PersistenceError("the assistant message is no longer pending")
        return version

    async def write_checkpoint(
        self,
        message_id: str,
        content: str,
        expected_version: int,
    ) -> int | None:
        """Conditionally persist streamed content.

        ``None`` means the write was rejected as stale or already terminal.
        """
        async with self._bounded(
            "the checkpoint write timed out",
            "the checkpoint could not be persisted",
        ):
            return await self._repo.write_checkpoint(
                message_id,
                content=content,
                expected_version=expected_version,
            )

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
        """Persist final content and a terminal status; confirm the result."""
        async with self._bounded(
            "the terminal conversation state timed out",
            "the terminal conversation state could not be persisted",
        ):
            return await self._repo.finalize(
                message_id,
                status=status,
                content=content,
                expected_version=expected_version,
                error=error,
                tokens=tokens or None,
                provider=provider or None,
                model=model or None,
                privacy=privacy or None,
            )

    async def reconcile(self, request_id: str) -> PersistedTurn | None:
        """Look up a turn by request UUID in a fresh session."""
        return await self._reconcile_quiet(request_id)

    async def reconcile_stale_generations(self) -> int:
        """Demote prior-session ``pending``/``streaming`` rows to ``interrupted``."""
        async with self._bounded(
            "reconciling interrupted generations timed out",
            "interrupted generations could not be reconciled",
        ):
            return await self._repo.mark_stale_open_interrupted(
                current_session_id=self._session_id
            )

    async def _reconcile_quiet(self, request_id: str) -> PersistedTurn | None:
        try:
            async with asyncio.timeout(self._deadline):
                return await self._repo.get_turn(request_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - absence is not proof of absence
            log.warning("turn reconciliation failed", exc_info=True)
            return None

    def _schedule_reconcile(self, request_id: str) -> None:
        """Best-effort reconciliation that survives task cancellation."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._reconcile_quiet(request_id))
        self._reconcile_tasks.add(task)
        task.add_done_callback(self._on_reconcile_done)

    def _on_reconcile_done(self, task: asyncio.Task[PersistedTurn | None]) -> None:
        self._reconcile_tasks.discard(task)
        if not task.cancelled():
            with contextlib.suppress(BaseException):
                task.exception()

    async def drain_reconcile(self) -> None:
        """Await any in-flight reconciliation tasks (bounded)."""
        for _ in range(4):
            pending = [t for t in self._reconcile_tasks if not t.done()]
            if not pending:
                return
            try:
                async with asyncio.timeout(self._deadline):
                    await asyncio.gather(*pending, return_exceptions=True)
            except (TimeoutError, asyncio.CancelledError):
                for task in pending:
                    task.cancel()
                with contextlib.suppress(BaseException):
                    await asyncio.gather(*pending, return_exceptions=True)
                return

    async def close(self) -> None:
        """Drain background reconciliation tasks before database shutdown."""
        await self.drain_reconcile()

    # -- Reads -----------------------------------------------------------------

    async def load_conversation(
        self, conversation_id: str, *, limit: int = 200
    ) -> list[StoredMessage]:
        async with self._bounded(
            "loading the conversation timed out",
            "the conversation could not be loaded",
        ):
            return await self._repo.load_conversation(conversation_id, limit=limit)

    async def list_conversations(
        self, *, limit: int = 100
    ) -> list[ConversationSummary]:
        async with self._bounded(
            "listing conversations timed out",
            "conversations could not be listed",
        ):
            return await self._repo.list_conversations(limit=limit)

    async def get_conversation(
        self, conversation_id: str
    ) -> ConversationSummary | None:
        async with self._bounded(
            "loading the conversation summary timed out",
            "the conversation summary could not be loaded",
        ):
            return await self._repo.get_conversation(conversation_id)

    async def set_active_conversation(self, conversation_id: str) -> None:
        async with self._bounded(
            "updating the active conversation timed out",
            "the active conversation could not be updated",
        ):
            await self._repo.set_active_conversation(conversation_id)

    async def get_active_conversation_id(self) -> str | None:
        async with self._bounded(
            "reading the active conversation timed out",
            "the active conversation could not be read",
        ):
            return await self._repo.get_active_conversation_id()

    async def rename_conversation(
        self, conversation_id: str, title: str
    ) -> ConversationSummary:
        async with self._bounded(
            "renaming the conversation timed out",
            "the conversation could not be renamed",
        ):
            return await self._repo.rename_conversation(conversation_id, title)

    async def delete_conversation(self, conversation_id: str) -> bool:
        async with self._bounded(
            "deleting the conversation timed out",
            "the conversation could not be deleted",
        ):
            return await self._repo.delete_conversation(conversation_id)

    # -- Lifecycle -------------------------------------------------------------

    @contextlib.asynccontextmanager
    async def _bounded(
        self, timeout_message: str, error_message: str
    ) -> AsyncIterator[None]:
        """Run a repository operation under the deadline and normalize errors."""
        try:
            async with asyncio.timeout(self._deadline):
                yield
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            raise PersistenceTimeoutError(timeout_message) from exc
        except ConversationError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalized below
            raise PersistenceError(error_message) from exc

    @contextlib.asynccontextmanager
    async def deadline(self) -> AsyncIterator[None]:
        """Expose the configured budget to callers that compose operations."""
        async with asyncio.timeout(self._deadline):
            yield


__all__ = ["ConversationService"]
