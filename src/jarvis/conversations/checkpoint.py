"""Per-message persistence coordinator.

Serialises checkpoint writes, the final content flush and the terminal status
update for exactly one assistant message. This is deliberately separate from
the UI repaint coalescer in ``ChatController``: repaint throttling is a
presentation concern, checkpoint throttling is a durability concern.

Policy:

- at most one checkpoint per ``min_interval`` seconds;
- at least ``min_chars`` new characters before a checkpoint (except on
  finalisation);
- at most one in-flight checkpoint per message;
- no writes at all after terminal finalisation;
- every write is conditional on the expected content version, so a stale
  checkpoint can never overwrite newer content or a terminal state.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from typing import Any

from jarvis.monitoring.logging import get_logger

log = get_logger(__name__)

DEFAULT_MIN_INTERVAL_SECONDS = 1.0
DEFAULT_MIN_CHARS = 256
DEFAULT_FINALIZE_WAIT_SECONDS = 2.0

CheckpointFn = Callable[[str, int], Awaitable[int | None]]
FinalizeFn = Callable[[str, int, str, str | None, int], Awaitable[bool]]


class PersistenceCoordinator:
    """Durable, throttled persistence for one assistant message."""

    def __init__(
        self,
        *,
        checkpoint: CheckpointFn,
        finalize: FinalizeFn,
        version: int,
        clock: Callable[[], float] = time.monotonic,
        min_interval: float = DEFAULT_MIN_INTERVAL_SECONDS,
        min_chars: int = DEFAULT_MIN_CHARS,
        finalize_wait: float = DEFAULT_FINALIZE_WAIT_SECONDS,
        scheduler: Callable[[Awaitable[Any]], asyncio.Task | None] | None = None,
    ) -> None:
        self._checkpoint = checkpoint
        self._finalize = finalize
        self._version = int(version)
        self._clock = clock
        self._min_interval = float(min_interval)
        self._min_chars = int(min_chars)
        self._finalize_wait = float(finalize_wait)
        self._scheduler = scheduler

        self._content = ""
        self._persisted_len = 0
        self._last_start = float("-inf")
        self._inflight: asyncio.Task | None = None
        self._dirty = False
        self._terminal = False

        self.last_error: BaseException | None = None
        self.checkpoints_written = 0
        self.stale_rejections = 0

    # -- State -----------------------------------------------------------------

    @property
    def version(self) -> int:
        return self._version

    @property
    def terminal(self) -> bool:
        return self._terminal

    @property
    def inflight(self) -> bool:
        return self._inflight is not None and not self._inflight.done()

    def set_version(self, version: int) -> None:
        """Adopt a version produced by a transition outside the coordinator."""
        self._version = int(version)

    # -- Streaming -------------------------------------------------------------

    def observe(self, content: str) -> None:
        """Accept the current accumulated content (called per token)."""
        if self._terminal:
            return
        self._content = content
        if self.inflight:
            self._dirty = True
            return
        now = self._clock()
        if now - self._last_start < self._min_interval:
            return
        if len(content) - self._persisted_len < self._min_chars:
            return
        self._start(now)

    def _start(self, now: float) -> None:
        content = self._content
        version = self._version
        task = self._spawn(self._write(content, version))
        if task is None:
            return
        self._last_start = now
        self._dirty = False
        self._inflight = task
        task.add_done_callback(self._on_done)

    def _on_done(self, task: asyncio.Task) -> None:
        if self._inflight is task:
            self._inflight = None

    async def _write(self, content: str, version: int) -> None:
        try:
            new_version = await self._checkpoint(content, version)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced via last_error
            self.last_error = exc
            log.warning("checkpoint write failed", exc_info=True)
            return
        if new_version is None:
            # Stale or already terminal: never overwrite. The version is left
            # untouched so a later write can still succeed.
            self.stale_rejections += 1
            return
        self._version = int(new_version)
        self._persisted_len = max(self._persisted_len, len(content))
        self.checkpoints_written += 1

    # -- Terminal --------------------------------------------------------------

    async def close_and_finalize(
        self,
        *,
        status: str,
        content: str,
        error: str | None = None,
        tokens: int = 0,
    ) -> bool:
        """Stop accepting content, flush, and persist the terminal state.

        Returns ``True`` only when the durable terminal state is confirmed.
        """
        self._terminal = True
        self._content = content
        await self._drain_inflight()

        try:
            confirmed = await self._finalize(
                content, self._version, status, error, tokens
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            self.last_error = exc
            log.warning("terminal persistence failed", exc_info=True)
            return False
        if confirmed:
            self._persisted_len = len(content)
        return bool(confirmed)

    async def _drain_inflight(self) -> None:
        task = self._inflight
        if task is None or task.done():
            self._inflight = None
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=self._finalize_wait)
        except (TimeoutError, asyncio.CancelledError):
            # Invalidate outstanding work: its result can no longer be trusted
            # to reflect the final buffer.
            task.cancel()
            with contextlib.suppress(BaseException):
                await asyncio.gather(task, return_exceptions=True)
        finally:
            self._inflight = None

    def abandon(self) -> None:
        """Invalidate any in-flight checkpoint without waiting for it."""
        self._terminal = True
        task = self._inflight
        self._inflight = None
        if task is not None and not task.done():
            task.cancel()

    # -- Helpers ---------------------------------------------------------------

    def _spawn(self, coro: Awaitable[Any]) -> asyncio.Task | None:
        if self._scheduler is not None:
            return self._scheduler(coro)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            close = getattr(coro, "close", None)
            if close is not None:
                close()
            return None
        return loop.create_task(coro)
