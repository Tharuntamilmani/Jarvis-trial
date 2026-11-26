"""Workspace chat controller (Stage 2.6).

The controller owns the in-memory conversation and the request lifecycle.
It never talks to providers directly: chat requests go through
:class:`~jarvis.intelligence.chat_service.ChatService`, and stream events
arrive back through :class:`~jarvis.desktop.adapters.event_bridge.EventBridge`
via :class:`~jarvis.desktop.viewmodels.app_state.AppState`.

Design rules:

- request IDs are generated before dispatch and passed explicitly to
  ``ChatService.start_chat``;
- events for any other request are ignored (request-ID filtering);
- duplicate/out-of-order token sequences are dropped;
- exactly one terminal state is applied per request;
- partial content is preserved on failure/cancellation;
- a Stop before ``start_chat`` returns is recorded and cancelled exactly
  once as soon as registration completes;
- the consent responder is bound to the active request and fails closed.

The controller is independent of the widget tree and outlives disposable
Workspace windows, so conversation state survives close/reopen and
tray-only operation.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Protocol, TypeVar

from PySide6.QtCore import QObject, Signal

from jarvis.core.engine import ProviderStatus
from jarvis.core.events import CloudFallbackProposed
from jarvis.core.exceptions import KillSwitchActiveError
from jarvis.conversations.checkpoint import PersistenceCoordinator
from jarvis.conversations.models import (
    ConversationSummary,
    ErrorCategory,
    MessageStatus,
    PersistedTurn,
    StoredMessage,
    safe_error_display,
)
from jarvis.intelligence.chat_service import ChatService, ChatServiceError
from jarvis.intelligence.cloud_payload import CloudPayload
from jarvis.intelligence.consent import ConsentManager
from jarvis.monitoring.logging import get_logger
from jarvis.desktop.viewmodels.app_state import AppState
from jarvis.desktop.viewmodels.chat_models import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_PENDING,
    STATUS_STREAMING,
    ConsentRequest,
    ConversationMessage,
    build_local_history,
)

log = get_logger(__name__)

# Token updates are coalesced to roughly 25 UI frames per second. The
# message model always contains every accepted token; only repaint signals
# are throttled so high-token-rate streams cannot starve the Qt loop.
TOKEN_FLUSH_INTERVAL_SECONDS = 0.04
_MAX_ERROR_DETAIL = 240

T = TypeVar("T")


class ProviderStatusSource(Protocol):
    """The additive CoreEngine provider-status facade consumed by the UI."""

    def provider_status_snapshot(self) -> dict[str, ProviderStatus]: ...

    async def refresh_provider_statuses(self) -> dict[str, ProviderStatus]: ...


class ConversationPersistence(Protocol):
    """Durability boundary consumed by the controller (Stage 2.8).

    Implemented by ``jarvis.conversations.service.ConversationService``. The
    controller never issues SQL; it only requests these operations.
    """

    async def persist_initial_turn(
        self,
        *,
        request_id: str,
        user_content: str,
        conversation_id: str | None = None,
    ) -> PersistedTurn: ...

    async def mark_streaming(self, turn: PersistedTurn) -> int: ...

    async def write_checkpoint(
        self, message_id: str, content: str, expected_version: int
    ) -> int | None: ...

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
    ) -> bool: ...

    async def load_conversation(
        self, conversation_id: str, *, limit: int = 200
    ) -> list[StoredMessage]: ...

    async def get_active_conversation_id(self) -> str | None: ...


# Bounded wait for outstanding terminal persistence during shutdown. Each
# underlying operation already has its own deadline; this is a final guard.
_SHUTDOWN_FINALIZE_TIMEOUT = 5.0


class ChatController(QObject):
    """Conversation state, cancellation, consent, and provider status."""

    messages_changed = Signal()
    message_updated = Signal(object)  # ConversationMessage
    conversation_reset = Signal()
    conversations_changed = Signal()
    status_changed = Signal()
    busy_changed = Signal(bool)
    consent_requested = Signal(object)  # ConsentRequest
    consent_dismissed = Signal()
    provider_status_changed = Signal(object)  # dict[str, ProviderStatus]
    error_occurred = Signal(str)
    input_restored = Signal(str)  # restore composer text after a save failure

    def __init__(
        self,
        state: AppState,
        chat_service: ChatService | None = None,
        consent: ConsentManager | None = None,
        *,
        provider_source: ProviderStatusSource | None = None,
        conversations: ConversationPersistence | None = None,
        conversation_id: str | None = None,
        request_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        scheduler: Callable[[Awaitable[Any]], asyncio.Task | None] | None = None,
        install_consent: bool = True,
    ) -> None:
        super().__init__()
        self._state = state
        self._chat_service = chat_service
        self._consent = consent
        self._provider_source = provider_source
        self._persistence = conversations
        self._conversation_id = conversation_id
        self._new_request_id = request_id_factory or (lambda: uuid.uuid4().hex)
        self._clock = clock or time.monotonic
        self._scheduler = scheduler

        self._messages: list[ConversationMessage] = []
        self._active_request_id: str | None = None
        self._active_message_id: str | None = None
        self._next_token_seq = 0
        self._last_token_emit = 0.0
        self._busy = False
        self._closing = False
        # True when persistence is configured but unavailable at startup.
        self._persistence_unavailable = False

        self._provider = ""
        self._model = ""
        self._privacy = ""

        self._registered_requests: set[str] = set()
        self._cancel_intents: set[str] = set()
        self._cancel_issued: set[str] = set()
        self._cancel_tasks: dict[str, asyncio.Task] = {}
        self._dispatch_task: asyncio.Task | None = None
        self._status_task: asyncio.Task | None = None

        self._consent_installed = False
        self._consent_future: asyncio.Future[bool] | None = None
        self._consent_request: ConsentRequest | None = None

        self._first_workspace_show = False
        self._provider_statuses: dict[str, ProviderStatus] = {}
        self._load_cached_provider_statuses()

        # Stage 2.8 durability state.
        self._turn: PersistedTurn | None = None
        self._coordinator: PersistenceCoordinator | None = None
        self._optimistic_ids: tuple[str, str] | None = None
        self._pending_user_content = ""
        self._finalize_tasks: dict[str, asyncio.Task] = {}
        self._finalize_confirmed: dict[str, bool] = {}
        self._reset_task: asyncio.Task | None = None
        self._finalizing = False
        # Conversation ID snapshot at the start of a finalize-and-reset cycle.
        self._finalizing_conversation_id: str | None = None
        # Track unfinalized assistant-message state independently of
        # _active_request_id so a failed finalization remains retryable.
        self._unfinalized_message: ConversationMessage | None = None
        self._unfinalized_request_id: str | None = None
        # Reconciliation tasks are tracked and drained by ConversationService.
        # True while load_conversation holds ownership of the conversation.
        self._loading_conversation = False
        self._conversations_list: list[ConversationSummary] = []
        self._selected_provider: str | None = None
        self._selected_model: str | None = None
        self._available_models: dict[str, list[str]] = {}
        self._recovering_persistence = False

        self._connect_state()
        if install_consent and consent is not None:
            self.install_consent_responder()

    # -- Read-only state -------------------------------------------------------

    @property
    def messages(self) -> tuple[ConversationMessage, ...]:
        return tuple(self._messages)

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def closing(self) -> bool:
        return self._closing

    @property
    def active_request_id(self) -> str | None:
        return self._active_request_id

    @property
    def assistant_state(self) -> str:
        return self._state.assistant_state

    @property
    def kill_switch_active(self) -> bool:
        return self._state.kill_switch_active

    @property
    def service_available(self) -> bool:
        return self._chat_service is not None

    @property
    def active_provider(self) -> str:
        return self._provider

    @property
    def active_model(self) -> str:
        return self._model

    @property
    def active_privacy(self) -> str:
        return self._privacy

    @property
    def provider_statuses(self) -> dict[str, ProviderStatus]:
        return dict(self._provider_statuses)

    @property
    def current_consent(self) -> ConsentRequest | None:
        return self._consent_request

    @property
    def conversation_id(self) -> str | None:
        """Persisted conversation for this view, or ``None`` for a draft."""
        return self._conversation_id

    @property
    def conversations_list(self) -> tuple[ConversationSummary, ...]:
        """Cached conversation summaries for sidebar and History view."""
        return tuple(self._conversations_list)

    @property
    def selected_provider(self) -> str | None:
        """Explicit provider override (``None`` means local-first auto)."""
        return self._selected_provider

    @property
    def selected_model(self) -> str | None:
        """Explicit model override (``None`` means provider default)."""
        return self._selected_model

    @property
    def available_models(self) -> dict[str, list[str]]:
        return {k: list(v) for k, v in self._available_models.items()}

    def set_model_selection(
        self, provider: str | None, model: str | None = None
    ) -> None:
        """Set explicit provider/model routing override for future turns."""
        clean_provider = provider.strip() if provider else None
        clean_model = model.strip() if model else None
        if (
            clean_provider == self._selected_provider
            and clean_model == self._selected_model
        ):
            return
        self._selected_provider = clean_provider or None
        self._selected_model = clean_model or None
        self.status_changed.emit()

    async def refresh_available_models(self) -> dict[str, list[str]]:
        """Query CoreEngine for available models per provider."""
        if self._closing or self._provider_source is None:
            return dict(self._available_models)
        list_fn = getattr(self._provider_source, "list_available_models", None)
        if list_fn is None:
            return dict(self._available_models)
        try:
            models = await list_fn()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.warning("model list refresh failed", exc_info=True)
            return dict(self._available_models)
        self._available_models = {k: list(v) for k, v in models.items()}
        self.status_changed.emit()
        return dict(self._available_models)

    @property
    def finalizing(self) -> bool:
        """True while a New Chat switch waits for durable finalisation."""
        return self._finalizing

    @property
    def persistence_available(self) -> bool:
        """True when a live persistence service is attached."""
        return (
            self._persistence is not None
            and not self._persistence_unavailable
        )

    @property
    def persistence_unavailable(self) -> bool:
        """True when conversation persistence is missing or unreachable."""
        return not self.persistence_available

    def mark_persistence_unavailable(self) -> None:
        """Record that conversation persistence cannot be used."""
        if self._persistence_unavailable:
            return
        self._persistence_unavailable = True
        self.status_changed.emit()

    def can_send(self, text: str = "") -> bool:
        return (
            not self._closing
            and not self._busy
            and not self._finalizing
            and self.persistence_available
            and self._chat_service is not None
            and not self.kill_switch_active
            and bool(text.strip())
        )

    # -- Sending ---------------------------------------------------------------

    def send(self, text: str) -> str | None:
        """Start one chat turn; returns the request ID or ``None``.

        The dispatch itself runs on the asyncio loop, so Qt never blocks.
        """
        content = text.strip()
        if not content:
            return None
        if self._closing or self._busy or self._finalizing:
            return None
        if self.persistence_unavailable:
            self.error_occurred.emit(
                "Conversation persistence is unavailable. Messages cannot "
                "be sent until the database is reachable."
            )
            return None
        if self._chat_service is None:
            self.error_occurred.emit("Chat service is unavailable.")
            return None
        if self.kill_switch_active:
            self.error_occurred.emit(
                "Emergency stop is engaged; chat requests are blocked."
            )
            return None

        request_id = self._new_request_id()
        now = datetime.now()
        user_message = ConversationMessage(
            id=uuid.uuid4().hex,
            role="user",
            content=content,
            created_at=now,
            status=STATUS_COMPLETED,
            request_id=request_id,
        )
        assistant_message = ConversationMessage(
            id=uuid.uuid4().hex,
            role="assistant",
            content="",
            created_at=now,
            status=STATUS_PENDING,
            request_id=request_id,
        )
        self._messages.extend((user_message, assistant_message))
        self._optimistic_ids = (user_message.id, assistant_message.id)
        self._pending_user_content = content
        self._coordinator = None
        self._turn = None

        self._active_request_id = request_id
        self._active_message_id = assistant_message.id
        self._next_token_seq = 0
        # The first token always repaints; later tokens are coalesced.
        self._last_token_emit = (
            self._clock() - TOKEN_FLUSH_INTERVAL_SECONDS - 1.0
        )
        self._cancel_intents.discard(request_id)
        self._provider = ""
        self._model = ""
        self._privacy = ""
        self._set_busy(True)
        self.messages_changed.emit()
        self.status_changed.emit()

        # Excludes the streaming placeholder; includes the new user turn.
        history = build_local_history(self._messages[:-1])
        self._dispatch_task = self._spawn(
            self._dispatch(request_id, history, content)
        )
        return request_id

    def can_retry(self, message_id: str) -> bool:
        """True when an assistant message can be explicitly retried."""
        if (
            self._closing
            or self._busy
            or self._finalizing
            or self.persistence_unavailable
            or self._chat_service is None
            or self.kill_switch_active
            or self._conversation_id is None
        ):
            return False
        target = next(
            (m for m in self._messages if m.id == message_id), None
        )
        if target is None or target.role != "assistant":
            return False
        if target.status not in (
            STATUS_FAILED,
            STATUS_CANCELLED,
            STATUS_INTERRUPTED,
        ):
            return False
        return bool(target.request_id)

    def retry(self, message_id: str) -> str | None:
        """Explicitly retry a failed, cancelled, or interrupted assistant turn."""
        if not self.can_retry(message_id):
            return None
        target = next(m for m in self._messages if m.id == message_id)
        conversation_id = self._conversation_id
        assert conversation_id is not None

        request_id = self._new_request_id()
        assistant_message = ConversationMessage(
            id=uuid.uuid4().hex,
            role="assistant",
            content="",
            created_at=datetime.now(),
            status=STATUS_PENDING,
            request_id=request_id,
        )
        self._messages.append(assistant_message)
        self._optimistic_ids = (assistant_message.id,)
        self._pending_user_content = ""
        self._coordinator = None
        self._turn = None

        self._active_request_id = request_id
        self._active_message_id = assistant_message.id
        self._next_token_seq = 0
        self._last_token_emit = (
            self._clock() - TOKEN_FLUSH_INTERVAL_SECONDS - 1.0
        )
        self._cancel_intents.discard(request_id)
        self._provider = ""
        self._model = ""
        self._privacy = ""
        self._set_busy(True)
        self.messages_changed.emit()
        self.status_changed.emit()

        history = build_local_history(self._messages[:-1])
        self._dispatch_task = self._spawn(
            self._dispatch_retry(
                request_id,
                conversation_id=conversation_id,
                retry_of_request_id=target.request_id,
                history=history,
            )
        )
        return request_id

    async def _dispatch(
        self, request_id: str, history: list[Any], content: str = ""
    ) -> None:
        persistence = self._persistence
        if persistence is None:
            self._on_persistence_failed(
                request_id,
                RuntimeError("conversation persistence unavailable"),
            )
            return
        await self._run_turn_dispatch(
            request_id,
            history,
            lambda: persistence.persist_initial_turn(
                request_id=request_id,
                user_content=content,
                conversation_id=self._conversation_id,
            ),
        )

    async def _dispatch_retry(
        self,
        request_id: str,
        *,
        conversation_id: str,
        retry_of_request_id: str,
        history: list[Any],
    ) -> None:
        persistence = self._persistence
        if persistence is None:
            self._on_persistence_failed(
                request_id,
                RuntimeError("conversation persistence unavailable"),
            )
            return
        await self._run_turn_dispatch(
            request_id,
            history,
            lambda: persistence.persist_retry(
                conversation_id=conversation_id,
                request_id=request_id,
                retry_of_request_id=retry_of_request_id,
            ),
        )

    async def _run_turn_dispatch(
        self,
        request_id: str,
        history: list[Any],
        persist_fn: Callable[[], Awaitable[PersistedTurn]],
    ) -> None:
        service = self._chat_service
        persistence = self._persistence
        if service is None:  # pragma: no cover - guarded by send()/retry()
            return
        try:
            if persistence is None or self._persistence_unavailable:
                self._on_persistence_failed(
                    request_id,
                    RuntimeError("conversation persistence unavailable"),
                )
                return

            # -- Durable gate: no provider request before a confirmed turn ----
            try:
                turn = await persist_fn()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - fail closed
                log.warning("turn persistence failed", exc_info=True)
                self._on_persistence_failed(request_id, exc)
                return

            if (
                self._active_request_id != request_id
                or request_id in self._cancel_intents
                or self._finalizing
                or self._closing
            ):
                # Superseded while persistence was in flight — the turn
                # committed, so finalize its assistant message as cancelled
                # before switching. Do not mutate _conversation_id so the
                # reset guard's snapshot remains valid.
                await self._finalize_superseded_turn(request_id, turn)
                return

            self._adopt_turn(turn)
            self._coordinator = PersistenceCoordinator(
                checkpoint=self._checkpoint_writer(turn),
                finalize=self._terminal_writer(turn),
                version=turn.assistant_content_version,
                scheduler=self._scheduler,
            )
            try:
                version = await persistence.mark_streaming(turn)
            except asyncio.CancelledError:
                self._fail_start(
                    request_id,
                    "Request cancelled.",
                    status=STATUS_CANCELLED,
                    error_category=ErrorCategory.CANCELLED.value,
                )
                raise
            except Exception as exc:  # noqa: BLE001 - no dispatch
                log.warning("dispatch state persistence failed", exc_info=True)
                self._fail_dispatch(request_id, exc)
                return

            self._coordinator.set_version(version)

            if (
                self._active_request_id != request_id
                or request_id in self._cancel_intents
                or self._finalizing
                or self._closing
            ):
                await self._finalize_superseded_turn(request_id, turn)
                return

            try:
                registered = await service.start_chat(
                    history,
                    provider=self._selected_provider,
                    model=self._selected_model,
                    request_id=request_id,
                )
            except asyncio.CancelledError:
                self._fail_start(
                    request_id,
                    "Request cancelled.",
                    status=STATUS_CANCELLED,
                    error_category=ErrorCategory.CANCELLED.value,
                )
                raise
            except KillSwitchActiveError:
                self._fail_start(
                    request_id,
                    "Emergency stop is engaged.",
                    error_category=ErrorCategory.PROVIDER_ERROR.value,
                )
                return
            except TimeoutError:
                self._fail_start(
                    request_id,
                    "Chat request timed out.",
                    error_category=ErrorCategory.TIMEOUT.value,
                )
                return
            except ChatServiceError as exc:
                self._fail_start(
                    request_id,
                    _safe_detail(exc),
                    error_category=ErrorCategory.PROVIDER_ERROR.value,
                )
                return
            except Exception as exc:  # noqa: BLE001 - never leak internals
                log.warning(
                    "chat dispatch failed for %s", request_id, exc_info=True
                )
                self._fail_start(
                    request_id,
                    f"Chat request could not be started ({type(exc).__name__}).",
                    error_category=ErrorCategory.PROVIDER_ERROR.value,
                )
                return

            self._registered_requests.add(registered)
            # A Stop or New Chat clicked before start_chat returned is
            # honoured here, exactly once. _cancel_intents is cleared by
            # _cancel_once's finally block after service.cancel() settles.
            if registered in self._cancel_intents:
                self._schedule_cancel(registered)
            elif (
                self._active_request_id != registered
                or self._finalizing
                or self._closing
            ):
                self._schedule_cancel(registered)
                self._registered_requests.discard(registered)
        finally:
            if self._dispatch_task is asyncio.current_task():
                self._dispatch_task = None

    async def _finalize_superseded_turn(
        self, request_id: str, turn: PersistedTurn
    ) -> None:
        """Finalize a committed turn that was superseded before dispatch."""
        coordinator = self._coordinator
        if coordinator is None:
            coordinator = PersistenceCoordinator(
                checkpoint=self._checkpoint_writer(turn),
                finalize=self._terminal_writer(turn),
                version=turn.assistant_content_version,
                scheduler=self._scheduler,
            )
            self._coordinator = coordinator
        message = self._active_message()
        if message is not None:
            message.status = STATUS_CANCELLED
            message.error_category = ErrorCategory.CANCELLED.value
            self.message_updated.emit(message)
        confirmed = await coordinator.close_and_finalize(
            status=MessageStatus.CANCELLED.value,
            content=message.content if message is not None else "",
            error=ErrorCategory.CANCELLED.value,
        )
        self._record_finalize_confirmed(request_id, confirmed)
        self._finish_active(request_id)
        # Never dispatched to provider, so no provider cancel is needed.
        self._cancel_intents.discard(request_id)

    def _fail_start(
        self,
        request_id: str,
        detail: str,
        *,
        status: str = STATUS_FAILED,
        error_category: str = ErrorCategory.PROVIDER_ERROR.value,
    ) -> None:
        if request_id != self._active_request_id:
            return
        message = self._active_message()
        if message is not None:
            message.status = status
            if status == STATUS_FAILED:
                message.error = detail[:_MAX_ERROR_DETAIL]
            message.error_category = error_category
            self.message_updated.emit(message)
        # Persist the terminal state so the assistant message does not remain
        # stuck in ``streaming`` or ``pending`` after restart.
        self._begin_finalize(
            request_id,
            message,
            status,
            error_category,
            0,
        )
        self._finish_active(request_id)

    # -- Persistence gate -------------------------------------------------------

    def _adopt_turn(self, turn: PersistedTurn) -> None:
        self._turn = turn
        self._conversation_id = turn.conversation_id
        if turn.created_conversation and not self._closing:
            self._spawn(self.refresh_conversations())

    def _checkpoint_writer(self, turn: PersistedTurn):
        persistence = self._persistence

        async def write(content: str, version: int) -> int | None:
            if persistence is None:  # pragma: no cover - guarded by caller
                return None
            return await persistence.write_checkpoint(
                turn.assistant_message_id, content, version
            )

        return write

    def _terminal_writer(self, turn: PersistedTurn):
        persistence = self._persistence

        async def write(
            content: str,
            version: int,
            status: str,
            error: str | None,
            tokens: int,
        ) -> bool:
            if persistence is None:  # pragma: no cover - guarded by caller
                return False
            return await persistence.finalize(
                turn.assistant_message_id,
                status=status,
                content=content,
                expected_version=version,
                error=error,
                tokens=tokens,
                provider=self._provider or None,
                model=self._model or None,
                privacy=self._privacy or None,
            )

        return write

    def _on_persistence_failed(
        self, request_id: str, exc: BaseException
    ) -> None:
        """Initial persistence failed: no rows exist, so undo the UI turn."""
        if request_id != self._active_request_id:
            return
        log.info("rolling back optimistic turn after save failure")
        restored = self._pending_user_content
        self._rollback_optimistic(request_id)
        self._record_finalize_confirmed(request_id, True)
        self.error_occurred.emit(
            "This message could not be saved to the conversation database. "
            "Nothing was sent to the model."
        )
        if restored:
            self.input_restored.emit(restored)

    def _rollback_optimistic(self, request_id: str) -> None:
        """Remove only this request's optimistic bubbles; keep the rest."""
        ids = self._optimistic_ids
        if ids is not None:
            kept = [
                message for message in self._messages if message.id not in ids
            ]
            if len(kept) != len(self._messages):
                self._messages[:] = kept
        self._optimistic_ids = None
        self._active_request_id = None
        self._active_message_id = None
        self._next_token_seq = 0
        self._coordinator = None
        self._turn = None
        self._registered_requests.discard(request_id)
        self._cancel_intents.discard(request_id)
        self._set_busy(False)
        self.messages_changed.emit()
        self.status_changed.emit()

    def _fail_dispatch(self, request_id: str, exc: BaseException) -> None:
        """The turn exists, but the dispatch marker was not confirmed."""
        if request_id != self._active_request_id:
            return
        message = self._active_message()
        if message is not None:
            message.status = STATUS_FAILED
            message.error_category = ErrorCategory.PERSISTENCE_ERROR.value
            message.error = (
                "The conversation could not be prepared for the model."
            )
            self.message_updated.emit(message)
        self._begin_finalize(
            request_id,
            message,
            MessageStatus.FAILED.value,
            ErrorCategory.PERSISTENCE_ERROR.value,
            0,
        )
        self._finish_active(request_id)
        self.error_occurred.emit(
            "This message could not be sent to the model."
        )

    # -- Stop / cancel ----------------------------------------------------------

    def stop(self) -> bool:
        """Request cancellation of the active turn (idempotent)."""
        if self._closing:
            return False
        request_id = self._active_request_id
        if request_id is None:
            return False
        self._deny_pending_consent(request_id)
        if request_id not in self._registered_requests:
            # Race: start_chat has not returned yet. _dispatch cancels it
            # immediately after registration.
            self._cancel_intents.add(request_id)
            return True
        self._schedule_cancel(request_id)
        return True

    def _schedule_cancel(self, request_id: str) -> None:
        if request_id in self._cancel_issued:
            return
        self._cancel_issued.add(request_id)
        task = self._spawn(self._cancel_once(request_id))
        if task is not None:
            self._cancel_tasks[request_id] = task
            task.add_done_callback(
                lambda _task, rid=request_id: self._cancel_tasks.pop(rid, None)
            )

    async def _cancel_once(self, request_id: str) -> None:
        service = self._chat_service
        if service is None:  # pragma: no cover - guarded by send()
            return
        try:
            await service.cancel(request_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - cancellation must not crash UI
            log.warning("cancel failed for %s", request_id, exc_info=True)
        finally:
            self._cancel_intents.discard(request_id)

    # -- Stream events ----------------------------------------------------------

    def _on_stream_started(
        self, request_id: str, provider: str, model: str, privacy: str
    ) -> None:
        if request_id != self._active_request_id:
            return
        self._provider = provider
        self._model = model
        self._privacy = privacy
        message = self._active_message()
        if message is not None:
            message.provider = provider
            message.model = model
            message.privacy = privacy
            if message.status == STATUS_STREAMING and not message.content:
                message.content = ""
            self.message_updated.emit(message)
        self.status_changed.emit()

    def _on_token(self, request_id: str, seq: int, text: str) -> None:
        if request_id != self._active_request_id or not text:
            return
        # Strict contiguity: duplicates, reordering, and gaps are dropped.
        if seq != self._next_token_seq:
            return
        self._next_token_seq += 1
        message = self._active_message()
        if message is None:
            return
        message.content += text
        message.status = STATUS_STREAMING
        if self._coordinator is not None:
            # Durability checkpointing is independent of repaint coalescing.
            self._coordinator.observe(message.content)
        now = self._clock()
        if now - self._last_token_emit >= TOKEN_FLUSH_INTERVAL_SECONDS:
            self._last_token_emit = now
            self.message_updated.emit(message)

    def _on_stream_completed(
        self, request_id: str, content: str, tokens_used: int
    ) -> None:
        if request_id != self._active_request_id:
            return
        message = self._active_message()
        if message is not None:
            message.content = content
            message.status = STATUS_COMPLETED
            message.tokens_used = tokens_used
            message.error = ""
            message.error_category = ""
            self.message_updated.emit(message)
        self._begin_finalize(
            request_id,
            message,
            MessageStatus.COMPLETED.value,
            None,
            tokens_used,
        )
        self._finish_active(request_id)

    def _on_stream_failed(
        self, request_id: str, detail: str, partial_content: str
    ) -> None:
        if request_id != self._active_request_id:
            return
        message = self._active_message()
        if message is not None:
            if partial_content:
                message.content = partial_content
            message.status = STATUS_FAILED
            message.error = (detail or "Chat request failed.")[
                :_MAX_ERROR_DETAIL
            ]
            message.error_category = ErrorCategory.PROVIDER_ERROR.value
            self.message_updated.emit(message)
        self._begin_finalize(
            request_id,
            message,
            MessageStatus.FAILED.value,
            ErrorCategory.PROVIDER_ERROR.value,
            0,
        )
        self._finish_active(request_id)

    def _on_stream_cancelled(
        self, request_id: str, partial_content: str
    ) -> None:
        if request_id != self._active_request_id:
            return
        message = self._active_message()
        if message is not None:
            if partial_content:
                message.content = partial_content
            message.status = STATUS_CANCELLED
            message.error_category = ErrorCategory.CANCELLED.value
            self.message_updated.emit(message)
        self._begin_finalize(
            request_id,
            message,
            MessageStatus.CANCELLED.value,
            ErrorCategory.CANCELLED.value,
            0,
        )
        self._finish_active(request_id)

    def _finish_active(self, request_id: str) -> None:
        if request_id != self._active_request_id:
            return
        self._deny_pending_consent(request_id)
        # Track the unfinalized message/request so New Chat knows
        # finalization is still pending even though _active_request_id
        # is about to be cleared.
        message = self._active_message()
        if message is not None and request_id not in self._finalize_confirmed:
            self._unfinalized_message = message
            self._unfinalized_request_id = request_id
        self._active_request_id = None
        self._active_message_id = None
        self._next_token_seq = 0
        self._registered_requests.discard(request_id)
        self._cancel_intents.discard(request_id)
        self._cancel_issued.discard(request_id)
        self._set_busy(False)
        self.status_changed.emit()

    def _active_message(self) -> ConversationMessage | None:
        if self._active_message_id is None:
            return None
        for message in self._messages:
            if message.id == self._active_message_id:
                return message
        return None

    # -- Terminal persistence ---------------------------------------------------

    def _begin_finalize(
        self,
        request_id: str,
        message: ConversationMessage | None,
        status: str,
        error_category: str | None,
        tokens: int,
    ) -> None:
        """Persist the terminal state off the Qt thread, tracked for idle waits."""
        coordinator = self._coordinator
        if self._persistence is None or coordinator is None:
            return
        if message is None:
            return
        task = self._spawn(
            coordinator.close_and_finalize(
                status=status,
                content=message.content,
                error=error_category,
                tokens=tokens,
            )
        )
        if task is None:
            return
        self._finalize_tasks[request_id] = task
        task.add_done_callback(
            lambda done, rid=request_id: self._on_finalize_done(rid, done)
        )

    def _record_finalize_confirmed(self, request_id: str, confirmed: bool) -> None:
        self._finalize_confirmed[request_id] = confirmed
        if confirmed and self._unfinalized_request_id == request_id:
            self._unfinalized_message = None
            self._unfinalized_request_id = None
        # Bound finalization bookkeeping so long-running sessions never leak.
        if len(self._finalize_confirmed) > 64:
            oldest = list(self._finalize_confirmed.keys())[:-32]
            for key in oldest:
                self._finalize_confirmed.pop(key, None)

    def _on_finalize_done(self, request_id: str, task: asyncio.Task) -> None:
        if self._finalize_tasks.get(request_id) is task:
            self._finalize_tasks.pop(request_id, None)
        if task.cancelled():
            self._record_finalize_confirmed(request_id, False)
            return
        error = task.exception()
        if error is not None:
            log.warning("terminal persistence failed", exc_info=error)
            self._record_finalize_confirmed(request_id, False)
            return
        self._record_finalize_confirmed(request_id, bool(task.result()))

    async def _await_finalize(self, request_id: str) -> bool:
        task = self._finalize_tasks.get(request_id)
        if task is not None and not task.done():
            await asyncio.gather(task, return_exceptions=True)
        return bool(self._finalize_confirmed.get(request_id, False))

    # -- Conversation -----------------------------------------------------------

    def new_chat(self) -> None:
        """Start a new draft conversation.

        The public API stays synchronous. When persistence is unavailable this
        refuses to discard unsaved conversation state. With an active
        generation the switch is deferred until cancellation *and* the durable
        terminal state are both confirmed.

        A second New Chat while finalizing is silently ignored so the in-flight
        reset is not bypassed. If ``_active_request_id`` is None but
        ``_unfinalized_message`` is set, finalization is still required.
        """
        if self._closing:
            return
        if self.persistence_unavailable:
            if self._messages or self._active_request_id is not None:
                self.error_occurred.emit(
                    "Conversation persistence is unavailable; current messages "
                    "were not discarded."
                )
            return
        if self._finalizing:
            return
        request_id = self._active_request_id
        # Even when _active_request_id is None, an unfinalized assistant
        # message means the stream completed but finalization has not been
        # confirmed. We must defer the reset until finalization settles.
        if request_id is None and self._unfinalized_message is None:
            self._reset_conversation()
            return
        # Use the unfinalized message's request_id if the stream ended.
        finalize_request_id = request_id or self._unfinalized_request_id
        if finalize_request_id is None:
            self._reset_conversation()
            return
        self._finalizing = True
        # Snapshot conversation identity so the reset only affects this one.
        self._finalizing_conversation_id = self._conversation_id
        if (
            request_id is not None
            and request_id not in self._registered_requests
        ):
            self._cancel_intents.add(request_id)
        self.status_changed.emit()
        task = self._spawn(self._finalize_and_reset(finalize_request_id))
        if task is None:
            self._finalizing = False
            self._finalizing_conversation_id = None
            self.status_changed.emit()
            return
        self._reset_task = task
        task.add_done_callback(self._on_reset_done)

    def _reset_conversation(self) -> None:
        request_id = self._active_request_id
        if request_id is not None:
            if request_id in self._registered_requests:
                self._schedule_cancel(request_id)
            else:
                self._cancel_intents.add(request_id)
        self._deny_pending_consent()
        self._messages.clear()
        self._active_request_id = None
        self._active_message_id = None
        self._next_token_seq = 0
        self._provider = ""
        self._model = ""
        self._privacy = ""
        self._coordinator = None
        self._turn = None
        self._optimistic_ids = None
        self._conversation_id = None
        self._set_busy(False)
        self.conversation_reset.emit()
        self.messages_changed.emit()
        self.status_changed.emit()

    async def _finalize_and_reset(self, request_id: str) -> None:
        # Snapshot the conversation and task identity that own this cycle.
        conversation_id = self._finalizing_conversation_id
        current_task = asyncio.current_task()
        self._deny_pending_consent(request_id)
        if request_id in self._registered_requests:
            self._schedule_cancel(request_id)
        else:
            self._cancel_intents.add(request_id)

        # Wait for an in-flight dispatch (persist_initial_turn / mark_streaming
        # / start_chat registration) to settle before finalizing or resetting.
        dispatch = self._dispatch_task
        if dispatch is not None and not dispatch.done():
            await asyncio.gather(dispatch, return_exceptions=True)

        for _ in range(3):
            pending = [
                task
                for task in self._cancel_tasks.values()
                if task is not None and not task.done()
            ]
            if not pending:
                break
            await asyncio.gather(*pending, return_exceptions=True)

        confirmed = await self._await_finalize(request_id)
        if not confirmed:
            confirmed = await self._finalize_partial_now(request_id)
        if not confirmed:
            self.error_occurred.emit(
                "The current conversation could not be finalized, so it was "
                "not switched. Your messages are unchanged."
            )
            return
        # Guard against stale callbacks or a conversation switched underneath us.
        if current_task is not None and self._reset_task is not current_task:
            return
        # Unconditional conversation identity guard: both None and non-None
        # snapshots are compared so a stale reset cannot touch a conversation
        # loaded or created by another operation.
        if self._conversation_id != conversation_id:
            return
        if self._loading_conversation:
            return
        if self._active_request_id not in (None, request_id):
            return
        self._unfinalized_message = None
        self._unfinalized_request_id = None
        self._reset_conversation()

    async def _finalize_partial_now(self, request_id: str) -> bool:
        """Finalise a still-open or previously unfinalized assistant message."""
        if self._active_request_id not in (None, request_id):
            return False
        message = self._active_message()
        if message is None and self._unfinalized_request_id == request_id:
            message = self._unfinalized_message
        coordinator = self._coordinator
        if message is None or coordinator is None:
            return True  # nothing durable to finalise
        if message.status in (STATUS_PENDING, STATUS_STREAMING):
            message.status = STATUS_CANCELLED
            message.error_category = ErrorCategory.CANCELLED.value
            self.message_updated.emit(message)
        target_status = (
            message.status
            if message.status
            in (STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED, STATUS_INTERRUPTED)
            else MessageStatus.CANCELLED.value
        )
        confirmed = await coordinator.close_and_finalize(
            status=target_status,
            content=message.content,
            error=message.error_category or None,
            tokens=message.tokens_used,
        )
        self._record_finalize_confirmed(request_id, confirmed)
        self._finish_active(request_id)
        return confirmed

    def _on_reset_done(self, task: asyncio.Task) -> None:
        # Only the task that started this finalization cycle may clear the flag.
        if self._reset_task is not task:
            return
        self._reset_task = None
        # Only clear _finalizing when the reset completed successfully or when
        # the task did run its cleanup. A failed finalization leaves the flag
        # set so the conversation is preserved — but we still must clear it here
        # since the cycle is over (the error was emitted in _finalize_and_reset).
        self._finalizing = False
        self._finalizing_conversation_id = None
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                log.warning("new chat finalization failed", exc_info=error)
        self.status_changed.emit()

    # -- Consent ---------------------------------------------------------------

    def install_consent_responder(self) -> None:
        if self._consent is None or self._consent_installed:
            return
        self._consent.set_responder(self._handle_consent_request)
        self._consent_installed = True

    def _detach_consent_responder(self) -> None:
        if self._consent is None or not self._consent_installed:
            return
        try:
            self._consent.clear_responder()
        except Exception:  # noqa: BLE001 - detach must never crash shutdown
            log.warning("consent responder detach failed", exc_info=True)
        self._consent_installed = False

    async def _handle_consent_request(
        self,
        proposal: CloudFallbackProposed,
        payload: CloudPayload,
    ) -> bool:
        """Show the consent card for the active request; fail closed."""
        if self._closing:
            return False
        request_id = proposal.request_id
        if not request_id or request_id != self._active_request_id:
            return False
        if payload.request_id != request_id:
            return False
        if (
            self._consent_future is not None
            and not self._consent_future.done()
        ):
            # Another prompt is already pending: never replace it.
            return False
        if not payload.user_message.strip():
            return False

        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        request = ConsentRequest(
            request_id=request_id,
            provider=proposal.provider,
            model=proposal.model or payload.model or "provider default",
            reason=proposal.reason or "Local model unavailable.",
            system_prompt=payload.system_prompt,
            user_message=payload.user_message,
            scope=payload.scope_description,
            fingerprint=payload.fingerprint,
            created_at=datetime.now(),
        )
        self._consent_future = future
        self._consent_request = request
        self.consent_requested.emit(request)
        try:
            return bool(await future)
        except asyncio.CancelledError:
            raise
        finally:
            if self._consent_future is future:
                self._consent_future = None
                self._consent_request = None
                self.consent_dismissed.emit()

    def approve_consent(self, request_id: str, fingerprint: str = "") -> bool:
        """Approve only the currently displayed, matching request."""
        return self._resolve_consent(request_id, fingerprint, True)

    def deny_consent(self, request_id: str, fingerprint: str = "") -> bool:
        """Reject the currently displayed request."""
        return self._resolve_consent(request_id, fingerprint, False)

    def _resolve_consent(
        self, request_id: str, fingerprint: str, granted: bool
    ) -> bool:
        if self._closing:
            return False  # late approvals can never grant consent
        request = self._consent_request
        future = self._consent_future
        if request is None or future is None or future.done():
            return False
        if request.request_id != request_id:
            return False
        if fingerprint and fingerprint != request.fingerprint:
            return False
        future.set_result(granted)
        return True

    def _deny_pending_consent(self, request_id: str | None = None) -> bool:
        request = self._consent_request
        future = self._consent_future
        if request is None or future is None or future.done():
            return False
        if request_id is not None and request.request_id != request_id:
            return False
        future.set_result(False)
        return True

    # -- Provider status --------------------------------------------------------

    def on_workspace_shown(self) -> None:
        """One real refresh on the first Workspace show (never repeated)."""
        if self._first_workspace_show:
            return
        self._first_workspace_show = True
        self.refresh_provider_status()

    def refresh_provider_status(self) -> None:
        """Manually request exactly one provider health refresh."""
        if self._closing or self._provider_source is None:
            return
        if self._status_task is not None and not self._status_task.done():
            return  # a refresh is already in flight
        self._status_task = self._spawn(self._refresh_provider_status_async())

    async def _refresh_provider_status_async(self) -> None:
        source = self._provider_source
        if source is None:
            return
        try:
            statuses = await source.refresh_provider_statuses()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - status must never crash the UI
            log.warning("provider status refresh failed", exc_info=True)
            return
        finally:
            if self._status_task is asyncio.current_task():
                self._status_task = None
        if self._closing:
            return
        self._provider_statuses = dict(statuses)
        self.provider_status_changed.emit(dict(self._provider_statuses))

    def _load_cached_provider_statuses(self) -> None:
        source = self._provider_source
        if source is None:
            return
        try:
            self._provider_statuses = dict(source.provider_status_snapshot())
        except Exception:  # noqa: BLE001 - cached display must never crash
            log.warning("cached provider status unavailable", exc_info=True)
            self._provider_statuses = {}

    def _on_provider_health(
        self, provider: str, available: bool, detail: str
    ) -> None:
        current = self._provider_statuses.get(provider)
        if current is None:
            status = ProviderStatus(
                provider=provider,
                available=available,
                detail=detail,
                privacy="",
                configured=False,
                model="",
            )
        else:
            status = ProviderStatus(
                provider=current.provider,
                available=available,
                detail=detail,
                privacy=current.privacy,
                configured=current.configured,
                model=current.model,
            )
        self._provider_statuses[provider] = status
        self.provider_status_changed.emit(dict(self._provider_statuses))

    # -- Conversation restoration -----------------------------------------------

    async def load_conversation(
        self, conversation_id: str, *, raise_on_error: bool = False
    ) -> bool:
        """Restore a persisted conversation and populate in-memory messages.

        Called by :class:`DesktopApplication` during startup (with
        ``raise_on_error=True`` so database failures fail closed) or by user
        actions. Returns ``True`` when messages were loaded.
        """
        if self._persistence is None or self._closing or self._finalizing:
            return False
        self._loading_conversation = True
        try:
            try:
                stored: list[StoredMessage] = (
                    await self._persistence.load_conversation(conversation_id)
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.warning("conversation reload failed", exc_info=True)
                if raise_on_error:
                    raise
                self.error_occurred.emit(
                    "The conversation could not be loaded from the database."
                )
                return False
            if self._closing or self._finalizing:
                return False
            if not stored:
                return False
            self._conversation_id = conversation_id
            self._messages.clear()
            for msg in stored:
                status = msg.status
                # Any assistant message stuck in a non-terminal state from a
                # previous session is demoted to interrupted so it is never
                # mistaken for an active generation.
                if msg.role == "assistant" and status in (
                    MessageStatus.PENDING.value,
                    MessageStatus.STREAMING.value,
                ):
                    status = STATUS_INTERRUPTED
                error_display = ""
                if msg.error:
                    error_display = safe_error_display(msg.error)
                self._messages.append(
                    ConversationMessage(
                        id=msg.id,
                        role=msg.role,
                        content=msg.content,
                        created_at=msg.created_at,
                        status=status,
                        provider=msg.provider or "",
                        model=msg.model or "",
                        privacy=msg.privacy or "",
                        error=error_display,
                        error_category=msg.error or "",
                        tokens_used=msg.tokens or 0,
                        request_id=msg.request_id or "",
                    )
                )
            self.messages_changed.emit()
            self.status_changed.emit()
            return True
        finally:
            self._loading_conversation = False

    async def refresh_conversations(self) -> list[ConversationSummary]:
        """Fetch persisted conversation summaries for sidebar/History view."""
        if self._persistence is None or self._closing or self._persistence_unavailable:
            return list(self._conversations_list)
        list_fn = getattr(self._persistence, "list_conversations", None)
        if list_fn is None:
            return []
        try:
            items = await list_fn(limit=100)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - non-fatal list refresh
            log.warning("conversation list refresh failed", exc_info=True)
            return list(self._conversations_list)
        self._conversations_list = list(items)
        self.conversations_changed.emit()
        return list(self._conversations_list)

    async def switch_conversation(self, conversation_id: str) -> bool:
        """Load a persisted conversation and update the active pointer."""
        if (
            self._persistence is None
            or self._closing
            or self._busy
            or self._finalizing
            or self._persistence_unavailable
        ):
            return False
        if conversation_id == self._conversation_id:
            return True
        loaded = await self.load_conversation(conversation_id)
        if not loaded:
            return False
        set_active = getattr(self._persistence, "set_active_conversation", None)
        if set_active is not None:
            try:
                await set_active(conversation_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.warning("failed to persist active conversation", exc_info=True)
        await self.refresh_conversations()
        return True

    async def rename_conversation(self, conversation_id: str, title: str) -> bool:
        """Rename a persisted conversation and refresh summaries."""
        if self._persistence is None or self._closing or self._persistence_unavailable:
            return False
        rename_fn = getattr(self._persistence, "rename_conversation", None)
        if rename_fn is None:
            return False
        try:
            await rename_fn(conversation_id, title)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.warning("conversation rename failed", exc_info=True)
            self.error_occurred.emit("The conversation could not be renamed.")
            return False
        await self.refresh_conversations()
        return True

    async def delete_conversation(self, conversation_id: str) -> bool:
        """Delete a persisted conversation; resets UI if it was active."""
        if (
            self._persistence is None
            or self._closing
            or self._persistence_unavailable
        ):
            return False
        if conversation_id == self._conversation_id and (
            self._busy or self._finalizing
        ):
            self.error_occurred.emit(
                "Stop the active generation before deleting this conversation."
            )
            return False
        delete_fn = getattr(self._persistence, "delete_conversation", None)
        if delete_fn is None:
            return False
        try:
            deleted = await delete_fn(conversation_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.warning("conversation delete failed", exc_info=True)
            self.error_occurred.emit("The conversation could not be deleted.")
            return False
        if deleted and conversation_id == self._conversation_id:
            self._reset_conversation()
        await self.refresh_conversations()
        return bool(deleted)

    async def retry_persistence_connection(self) -> bool:
        """Attempt bounded recovery when conversation persistence is unavailable."""
        if (
            self._persistence is None
            or self._closing
            or self._recovering_persistence
        ):
            return False
        self._recovering_persistence = True
        try:
            reconcile_stale = getattr(
                self._persistence, "reconcile_stale_generations", None
            )
            if reconcile_stale is not None:
                await reconcile_stale()
            active_id = await self._persistence.get_active_conversation_id()
            self._persistence_unavailable = False
            if active_id and not self._messages:
                await self.load_conversation(active_id, raise_on_error=True)
            await self.refresh_conversations()
            self.status_changed.emit()
            return True
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - stay fail-closed without leaking internals
            log.warning("database recovery attempt failed", exc_info=True)
            self._persistence_unavailable = True
            self.error_occurred.emit(
                "Database is still unreachable. Conversation persistence "
                "remains disabled."
            )
            self.status_changed.emit()
            return False
        finally:
            self._recovering_persistence = False

    # -- Lifecycle / shutdown ---------------------------------------------------

    def _connect_state(self) -> None:
        self._state.chat_stream_started.connect(self._on_stream_started)
        self._state.chat_token.connect(self._on_token)
        self._state.chat_stream_completed.connect(self._on_stream_completed)
        self._state.chat_stream_failed.connect(self._on_stream_failed)
        self._state.chat_stream_cancelled.connect(self._on_stream_cancelled)
        self._state.assistant_state_changed.connect(
            lambda _state: self.status_changed.emit()
        )
        self._state.kill_switch_changed.connect(self._on_kill_switch)
        self._state.provider_health_changed.connect(self._on_provider_health)

    def _on_kill_switch(self, active: bool) -> None:
        if active:
            self._deny_pending_consent()
        self.status_changed.emit()

    async def shutdown(self) -> None:
        """Deterministic teardown; late approvals can never grant consent."""
        if self._closing:
            return
        # 1. Closing state — all late UI decisions are rejected from here.
        self._closing = True
        # 2-3. Deny the pending prompt and resolve its future.
        self._deny_pending_consent()
        self.consent_dismissed.emit()
        # 4. Snapshot the request before cancelling dispatch: the dispatch
        # failure path clears _active_request_id, and the service request
        # must still be cancelled exactly once.
        request_id = self._active_request_id
        dispatch = self._dispatch_task
        if dispatch is not None and not dispatch.done():
            dispatch.cancel()
            await asyncio.gather(dispatch, return_exceptions=True)
        self._dispatch_task = None
        # 5. Cancel the active ChatService request exactly once.
        if request_id is not None:
            await self._cancel_for_shutdown(request_id)
        # 5b. Drain terminal persistence: the cancellation above publishes the
        # terminal event, which spawns the finalize task. It must not be
        # abandoned mid-write, but the wait is bounded.
        await self._drain_finalize_tasks()
        # 6. Clear pending UI state (conversation content is retained).
        self._active_request_id = None
        self._active_message_id = None
        self._next_token_seq = 0
        self._cancel_intents.clear()
        self._set_busy(False)
        self.status_changed.emit()
        # 7. Detach the consent responder.
        self._detach_consent_responder()

        status_task = self._status_task
        if status_task is not None and not status_task.done():
            status_task.cancel()
            await asyncio.gather(status_task, return_exceptions=True)
        self._status_task = None

        pending = [
            task
            for task in self._cancel_tasks.values()
            if task is not None and not task.done()
        ]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _drain_finalize_tasks(self) -> None:
        """Await outstanding terminal writes and reconciliation with a bound."""
        pending = [
            task
            for task in self._finalize_tasks.values()
            if task is not None and not task.done()
        ]
        reset = self._reset_task
        if reset is not None and not reset.done():
            pending.append(reset)
        if pending:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True),
                    timeout=_SHUTDOWN_FINALIZE_TIMEOUT,
                )
            except (TimeoutError, asyncio.CancelledError):
                for task in pending:
                    task.cancel()
                with contextlib.suppress(BaseException):
                    await asyncio.gather(*pending, return_exceptions=True)

        drain_reconcile = getattr(self._persistence, "drain_reconcile", None)
        if drain_reconcile is not None:
            try:
                await asyncio.wait_for(
                    drain_reconcile(), timeout=_SHUTDOWN_FINALIZE_TIMEOUT
                )
            except (TimeoutError, asyncio.CancelledError):
                pass

    async def _cancel_for_shutdown(self, request_id: str) -> None:
        service = self._chat_service
        if service is None:
            return
        existing = self._cancel_tasks.get(request_id)
        if existing is not None:
            if not existing.done():
                await asyncio.gather(existing, return_exceptions=True)
            return
        self._cancel_issued.add(request_id)
        try:
            await service.cancel(request_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - shutdown must stay deterministic
            log.warning(
                "shutdown cancel failed for %s", request_id, exc_info=True
            )

    # -- Helpers ---------------------------------------------------------------

    def wait_idle(self) -> Awaitable[None]:
        """Test/automation helper: wait for dispatch and cancel bookkeeping."""
        return self._wait_idle()

    async def _wait_idle(self) -> None:
        for _ in range(4):
            tasks = []
            if self._dispatch_task is not None and not self._dispatch_task.done():
                tasks.append(self._dispatch_task)
            tasks.extend(
                task
                for task in self._cancel_tasks.values()
                if task is not None and not task.done()
            )
            tasks.extend(
                task
                for task in self._finalize_tasks.values()
                if task is not None and not task.done()
            )
            if self._reset_task is not None and not self._reset_task.done():
                tasks.append(self._reset_task)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
                continue
            drain_reconcile = getattr(
                self._persistence, "drain_reconcile", None
            )
            if drain_reconcile is not None:
                await drain_reconcile()
            return

    def wait_provider_refresh(self) -> Awaitable[None]:
        return self._wait_provider_refresh()

    async def _wait_provider_refresh(self) -> None:
        task = self._status_task
        if task is not None and not task.done():
            await asyncio.gather(task, return_exceptions=True)

    def _set_busy(self, busy: bool) -> None:
        if busy == self._busy:
            return
        self._busy = busy
        self.busy_changed.emit(busy)

    def _spawn(self, coro: Awaitable[Any]) -> asyncio.Task | None:
        if self._scheduler is not None:
            return self._scheduler(coro)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.warning("no running asyncio loop; async UI action dropped")
            close = getattr(coro, "close", None)
            if close is not None:
                close()
            return None
        return loop.create_task(coro)


def _safe_detail(error: BaseException) -> str:
    detail = str(error).strip() or type(error).__name__
    return detail[:_MAX_ERROR_DETAIL]
