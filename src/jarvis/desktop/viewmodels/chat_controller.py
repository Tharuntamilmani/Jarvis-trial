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
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Protocol, TypeVar

from PySide6.QtCore import QObject, Signal

from jarvis.core.engine import ProviderStatus
from jarvis.core.events import CloudFallbackProposed
from jarvis.core.exceptions import KillSwitchActiveError
from jarvis.intelligence.chat_service import ChatService, ChatServiceError
from jarvis.intelligence.cloud_payload import CloudPayload
from jarvis.intelligence.consent import ConsentManager
from jarvis.monitoring.logging import get_logger
from jarvis.desktop.viewmodels.app_state import AppState
from jarvis.desktop.viewmodels.chat_models import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
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


class ChatController(QObject):
    """Conversation state, cancellation, consent, and provider status."""

    messages_changed = Signal()
    message_updated = Signal(object)  # ConversationMessage
    conversation_reset = Signal()
    status_changed = Signal()
    busy_changed = Signal(bool)
    consent_requested = Signal(object)  # ConsentRequest
    consent_dismissed = Signal()
    provider_status_changed = Signal(object)  # dict[str, ProviderStatus]
    error_occurred = Signal(str)

    def __init__(
        self,
        state: AppState,
        chat_service: ChatService | None = None,
        consent: ConsentManager | None = None,
        *,
        provider_source: ProviderStatusSource | None = None,
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

    def can_send(self, text: str = "") -> bool:
        return (
            not self._closing
            and not self._busy
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
        if self._closing or self._busy:
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
        )
        assistant_message = ConversationMessage(
            id=uuid.uuid4().hex,
            role="assistant",
            content="",
            created_at=now,
            status=STATUS_STREAMING,
        )
        self._messages.extend((user_message, assistant_message))

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
            self._dispatch(request_id, history)
        )
        return request_id

    async def _dispatch(self, request_id: str, history: list[Any]) -> None:
        service = self._chat_service
        if service is None:  # pragma: no cover - guarded by send()
            return
        try:
            registered = await service.start_chat(
                history, request_id=request_id
            )
        except asyncio.CancelledError:
            self._fail_start(request_id, "Request cancelled.")
            raise
        except KillSwitchActiveError:
            self._fail_start(request_id, "Emergency stop is engaged.")
            return
        except ChatServiceError as exc:
            self._fail_start(request_id, _safe_detail(exc))
            return
        except Exception as exc:  # noqa: BLE001 - never leak internals
            log.warning("chat dispatch failed for %s", request_id, exc_info=True)
            self._fail_start(
                request_id,
                f"Chat request could not be started ({type(exc).__name__}).",
            )
            return
        finally:
            if self._dispatch_task is asyncio.current_task():
                self._dispatch_task = None

        self._registered_requests.add(registered)
        # A Stop clicked before start_chat returned is honoured here,
        # exactly once.
        if registered in self._cancel_intents:
            self._cancel_intents.discard(registered)
            self._schedule_cancel(registered)
        elif self._active_request_id != registered:
            # The stream already reached its terminal state (or New Chat
            # reset) before start_chat returned; do not retain the ID.
            self._registered_requests.discard(registered)

    def _fail_start(self, request_id: str, detail: str) -> None:
        if request_id != self._active_request_id:
            return
        message = self._active_message()
        if message is not None:
            message.status = STATUS_FAILED
            message.error = detail[:_MAX_ERROR_DETAIL]
            self.message_updated.emit(message)
        self._finish_active(request_id)

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
            self.message_updated.emit(message)
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
            message.error = (detail or "Chat request failed.")[:_MAX_ERROR_DETAIL]
            self.message_updated.emit(message)
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
            self.message_updated.emit(message)
        self._finish_active(request_id)

    def _finish_active(self, request_id: str) -> None:
        if request_id != self._active_request_id:
            return
        self._deny_pending_consent(request_id)
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

    # -- Conversation -----------------------------------------------------------

    def new_chat(self) -> None:
        """Reset the in-memory conversation (cancels an active turn)."""
        if self._closing:
            return
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
        self._set_busy(False)
        self.conversation_reset.emit()
        self.messages_changed.emit()
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
        # 4. Cancel the UI dispatch task (start_chat may still be awaiting).
        dispatch = self._dispatch_task
        if dispatch is not None and not dispatch.done():
            dispatch.cancel()
            await asyncio.gather(dispatch, return_exceptions=True)
        self._dispatch_task = None
        # 5. Cancel the active ChatService request exactly once.
        request_id = self._active_request_id
        if request_id is not None:
            await self._cancel_for_shutdown(request_id)
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
        for _ in range(3):
            tasks = []
            if self._dispatch_task is not None and not self._dispatch_task.done():
                tasks.append(self._dispatch_task)
            tasks.extend(
                task
                for task in self._cancel_tasks.values()
                if task is not None and not task.done()
            )
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)

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
