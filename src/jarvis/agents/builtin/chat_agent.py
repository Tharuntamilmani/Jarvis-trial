"""ChatAgent: routes requests through ChatService (never bypasses consent)."""

from __future__ import annotations

import asyncio
import contextlib
import uuid

from jarvis.agents.base_agent import BaseAgent
from jarvis.agents.models import (
    AgentContext,
    AgentErrorCategory,
    AgentResult,
    AgentStatus,
)
from jarvis.core.event_bus import EventBus
from jarvis.core.events import (
    ChatStreamCancelled,
    ChatStreamCompleted,
    ChatStreamFailed,
)
from jarvis.intelligence.chat_service import ChatService
from jarvis.intelligence.providers.base import ChatMessage


class ChatAgent(BaseAgent):
    """Delegates chat turns to ``ChatService`` so routing/consent stay intact."""

    agent_id = "chat"
    name = "Chat Agent"
    capabilities = frozenset({"chat"})

    def __init__(self, chat_service: ChatService, bus: EventBus) -> None:
        self._chat = chat_service
        self._bus = bus

    async def run(self, context: AgentContext) -> AgentResult:
        req_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        done: asyncio.Future[AgentResult] = loop.create_future()

        def _on_completed(ev: ChatStreamCompleted) -> None:
            if ev.request_id == req_id and not done.done():
                done.set_result(
                    AgentResult(output=ev.content, status=AgentStatus.COMPLETED)
                )

        def _on_failed(ev: ChatStreamFailed) -> None:
            if ev.request_id == req_id and not done.done():
                done.set_result(
                    AgentResult(
                        output=ev.partial_content or ev.detail,
                        status=AgentStatus.FAILED,
                        error_category=AgentErrorCategory.EXECUTION_ERROR.value,
                    )
                )

        def _on_cancelled(ev: ChatStreamCancelled) -> None:
            if ev.request_id == req_id and not done.done():
                done.set_result(
                    AgentResult(
                        output=ev.partial_content,
                        status=AgentStatus.CANCELLED,
                        error_category=AgentErrorCategory.CANCELLED.value,
                    )
                )

        self._bus.subscribe(ChatStreamCompleted, _on_completed)
        self._bus.subscribe(ChatStreamFailed, _on_failed)
        self._bus.subscribe(ChatStreamCancelled, _on_cancelled)
        try:
            messages = [ChatMessage(role="user", content=context.input_text)]
            await self._chat.start_chat(messages, request_id=req_id)
            return await done
        except asyncio.CancelledError:
            with contextlib.suppress(BaseException):
                await self._chat.cancel(req_id)
            raise
        finally:
            self._bus.unsubscribe(ChatStreamCompleted, _on_completed)
            self._bus.unsubscribe(ChatStreamFailed, _on_failed)
            self._bus.unsubscribe(ChatStreamCancelled, _on_cancelled)
