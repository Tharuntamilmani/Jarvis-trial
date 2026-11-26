"""Agent run persistence and bounded execution manager (Phase 4.1)."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from jarvis.agents.base_agent import (
    AgentCapacityError,
    AgentError,
    AgentTransitionError,
)
from jarvis.agents.models import (
    TERMINAL_AGENT_STATUSES,
    VALID_AGENT_TRANSITIONS,
    AgentContext,
    AgentErrorCategory,
    AgentResult,
    AgentRunRecord,
    AgentStatus,
)
from jarvis.agents.registry import AgentRegistry
from jarvis.conversations.models import to_hex, to_uuid
from jarvis.core.event_bus import EventBus
from jarvis.core.events import (
    AgentRunFinished,
    AgentRunStarted,
    AgentStepUpdated,
)
from jarvis.core.exceptions import KillSwitchActiveError
from jarvis.database.connection import DatabaseService
from jarvis.database.models import AgentRun
from jarvis.monitoring.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from jarvis.core.lifecycle import LifecycleManager

log = get_logger(__name__)

_DEFAULT_MAX_OUTPUT_CHARS = 32_000
_DEFAULT_SHUTDOWN_TIMEOUT = 5.0


class AgentRunStore:
    """PostgreSQL persistence for ``agent_runs`` with strict state transitions."""

    def __init__(self, db: DatabaseService) -> None:
        self._db = db

    async def create_run(
        self,
        *,
        run_id: str | None = None,
        agent_id: str,
        input_text: str,
        owner_session_id: str | None = None,
        parent_run_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[AgentRunRecord, bool]:
        """Create a queued run or return an existing one for ``idempotency_key``.

        Returns ``(record, created)``.
        """
        run_uuid = to_uuid(run_id) if run_id else uuid.uuid4()
        owner_uuid = to_uuid(owner_session_id) if owner_session_id else None
        parent_uuid = to_uuid(parent_run_id) if parent_run_id else None
        clean_key = idempotency_key.strip() if idempotency_key else None

        try:
            async with self._db.session() as session:
                if clean_key:
                    existing = await self._load_by_key(session, clean_key)
                    if existing is not None:
                        return existing, False
                row = AgentRun(
                    id=run_uuid,
                    agent=agent_id,
                    input=input_text,
                    output=None,
                    status=AgentStatus.QUEUED.value,
                    duration_ms=None,
                    idempotency_key=clean_key,
                    error_category=None,
                    owner_session_id=owner_uuid,
                    parent_run_id=parent_uuid,
                )
                session.add(row)
                await session.flush()
                return _to_record(row), True
        except IntegrityError:
            if clean_key:
                existing = await self.get_by_idempotency_key(clean_key)
                if existing is not None:
                    return existing, False
            raise

    async def transition(
        self,
        run_id: str,
        *,
        to_status: AgentStatus,
        output: str | None = None,
        duration_ms: int | None = None,
        error_category: str | None = None,
    ) -> AgentRunRecord:
        """Atomically validate and apply a state transition."""
        run_uuid = to_uuid(run_id)
        async with self._db.session() as session:
            current_row = (
                await session.execute(
                    select(AgentRun)
                    .where(AgentRun.id == run_uuid)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if current_row is None:
                raise AgentError(f"agent run {run_id} does not exist")

            current_status = AgentStatus(current_row.status)
            allowed = VALID_AGENT_TRANSITIONS.get(current_status, frozenset())
            if to_status not in allowed:
                raise AgentTransitionError(
                    f"invalid transition {current_status.value!r} -> "
                    f"{to_status.value!r}"
                )

            values: dict[str, object] = {
                "status": to_status.value,
                "updated_at": func.now(),
            }
            if output is not None:
                values["output"] = output
            if duration_ms is not None:
                values["duration_ms"] = int(duration_ms)
            if error_category is not None:
                values["error_category"] = error_category

            updated = (
                await session.execute(
                    update(AgentRun)
                    .where(AgentRun.id == run_uuid)
                    .values(**values)
                    .returning(AgentRun)
                )
            ).scalar_one()
            return _to_record(updated)

    async def get_run(self, run_id: str) -> AgentRunRecord | None:
        run_uuid = to_uuid(run_id)
        async with self._db.session() as session:
            row = (
                await session.execute(
                    select(AgentRun).where(AgentRun.id == run_uuid)
                )
            ).scalar_one_or_none()
            return _to_record(row) if row is not None else None

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> AgentRunRecord | None:
        async with self._db.session() as session:
            return await self._load_by_key(session, idempotency_key)

    async def list_runs(self, *, limit: int = 50) -> list[AgentRunRecord]:
        async with self._db.session() as session:
            rows = (
                (
                    await session.execute(
                        select(AgentRun)
                        .order_by(
                            AgentRun.created_at.desc(), AgentRun.id.desc()
                        )
                        .limit(max(1, limit))
                    )
                )
                .scalars()
                .all()
            )
        return [_to_record(r) for r in rows]

    async def _load_by_key(
        self, session: object, key: str
    ) -> AgentRunRecord | None:
        row = (
            await session.execute(  # type: ignore[attr-defined]
                select(AgentRun).where(AgentRun.idempotency_key == key)
            )
        ).scalar_one_or_none()
        return _to_record(row) if row is not None else None


class AgentManager:
    """Single execution entry point for bounded, cancellable agent runs."""

    def __init__(
        self,
        registry: AgentRegistry,
        store: AgentRunStore,
        bus: EventBus,
        *,
        lifecycle: LifecycleManager | None = None,
        max_concurrent: int = 2,
        default_timeout_seconds: float = 60.0,
        max_output_chars: int = _DEFAULT_MAX_OUTPUT_CHARS,
        session_id: str | None = None,
    ) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self._registry = registry
        self._store = store
        self._bus = bus
        self._lifecycle = lifecycle
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._default_timeout = float(default_timeout_seconds)
        self._max_output_chars = int(max_output_chars)
        self._session_id = session_id or uuid.uuid4().hex
        self._closing = False
        self._tasks: dict[str, asyncio.Task[AgentRunRecord]] = {}

    @classmethod
    def from_database(
        cls,
        db: DatabaseService,
        bus: EventBus,
        *,
        registry: AgentRegistry | None = None,
        lifecycle: LifecycleManager | None = None,
        max_concurrent: int = 2,
        default_timeout_seconds: float = 60.0,
        session_id: str | None = None,
    ) -> AgentManager:
        return cls(
            registry=registry or AgentRegistry(),
            store=AgentRunStore(db),
            bus=bus,
            lifecycle=lifecycle,
            max_concurrent=max_concurrent,
            default_timeout_seconds=default_timeout_seconds,
            session_id=session_id,
        )

    @property
    def registry(self) -> AgentRegistry:
        return self._registry

    @property
    def active_run_ids(self) -> tuple[str, ...]:
        return tuple(self._tasks.keys())

    async def submit(
        self,
        agent_id: str,
        input_text: str,
        *,
        idempotency_key: str | None = None,
        parent_run_id: str | None = None,
        timeout_seconds: float | None = None,
        metadata: dict[str, object] | None = None,
    ) -> str:
        """Validate, durably queue, and schedule an agent run; returns ``run_id``."""
        if self._closing:
            raise AgentCapacityError("agent manager is shutting down")
        if self._lifecycle is not None and self._lifecycle.kill_switch_active:
            raise KillSwitchActiveError(
                "kill switch is active; agent runs are blocked"
            )
        agent = self._registry.get(agent_id)
        record, created = await self._store.create_run(
            agent_id=agent.agent_id,
            input_text=input_text,
            owner_session_id=self._session_id,
            parent_run_id=parent_run_id,
            idempotency_key=idempotency_key,
        )
        if not created:
            return record.id

        timeout = (
            float(timeout_seconds)
            if timeout_seconds is not None
            else self._default_timeout
        )
        context = AgentContext(
            run_id=record.id,
            agent_id=agent.agent_id,
            input_text=input_text,
            session_id=self._session_id,
            parent_run_id=parent_run_id,
            metadata=dict(metadata or {}),
        )
        task = asyncio.create_task(
            self._execute_run(record.id, agent, context, timeout)
        )
        self._tasks[record.id] = task
        task.add_done_callback(lambda _t, rid=record.id: self._tasks.pop(rid, None))
        if self._lifecycle is not None:
            self._lifecycle.register_killable_task(task)
        return record.id

    async def wait(self, run_id: str) -> AgentRunRecord:
        """Await completion of ``run_id`` and return its persisted record."""
        task = self._tasks.get(run_id)
        if task is not None:
            return await asyncio.shield(task)
        existing = await self._store.get_run(run_id)
        if existing is None:
            raise AgentError(f"unknown agent run: {run_id}")
        return existing

    async def cancel(self, run_id: str) -> bool:
        """Cancel a queued or running agent task."""
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        with contextlib.suppress(BaseException):
            await asyncio.gather(task, return_exceptions=True)
        return True

    async def report_step(
        self, run_id: str, agent_id: str, step: str, progress: float = 0.0
    ) -> None:
        await self._bus.publish(
            AgentStepUpdated(
                run_id=run_id,
                agent_id=agent_id,
                step=step,
                progress=progress,
            )
        )

    async def close(self) -> None:
        """Stop accepting work and cancel/drain all active agent tasks."""
        self._closing = True
        pending = [t for t in self._tasks.values() if not t.done()]
        if not pending:
            return
        for task in pending:
            task.cancel()
        try:
            async with asyncio.timeout(_DEFAULT_SHUTDOWN_TIMEOUT):
                await asyncio.gather(*pending, return_exceptions=True)
        except TimeoutError:
            log.warning("timed out draining %d agent run(s)", len(pending))

    async def _execute_run(
        self,
        run_id: str,
        agent: object,
        context: AgentContext,
        timeout: float,
    ) -> AgentRunRecord:
        started_at = time.monotonic()
        status = AgentStatus.FAILED
        output = ""
        error_category: str | None = None
        transitioned_running = False

        try:
            async with self._semaphore:
                if (
                    self._closing
                    or (
                        self._lifecycle is not None
                        and self._lifecycle.kill_switch_active
                    )
                ):
                    status = AgentStatus.CANCELLED
                    error_category = (
                        AgentErrorCategory.KILL_SWITCH.value
                        if (
                            self._lifecycle is not None
                            and self._lifecycle.kill_switch_active
                        )
                        else AgentErrorCategory.CANCELLED.value
                    )
                    return await self._store.transition(
                        run_id,
                        to_status=status,
                        duration_ms=0,
                        error_category=error_category,
                    )

                await self._store.transition(
                    run_id, to_status=AgentStatus.RUNNING
                )
                transitioned_running = True
                await self._bus.publish(
                    AgentRunStarted(
                        run_id=run_id,
                        agent_id=context.agent_id,
                        parent_run_id=context.parent_run_id or "",
                    )
                )

                async with asyncio.timeout(timeout):
                    result: AgentResult = await agent.run(context)  # type: ignore[attr-defined]
                output = (result.output or "")[: self._max_output_chars]
                status = (
                    result.status
                    if result.status in TERMINAL_AGENT_STATUSES
                    else AgentStatus.COMPLETED
                )
                error_category = result.error_category
        except asyncio.CancelledError:
            status = AgentStatus.CANCELLED
            error_category = (
                AgentErrorCategory.KILL_SWITCH.value
                if (
                    self._lifecycle is not None
                    and self._lifecycle.kill_switch_active
                )
                else AgentErrorCategory.CANCELLED.value
            )
            if not transitioned_running:
                return await self._finalize_quiet(
                    run_id, status=status, duration_ms=0, error_category=error_category
                )
            duration_ms = int((time.monotonic() - started_at) * 1000)
            return await self._finalize_quiet(
                run_id,
                status=status,
                output=output,
                duration_ms=duration_ms,
                error_category=error_category,
            )
        except TimeoutError:
            status = AgentStatus.TIMED_OUT
            error_category = AgentErrorCategory.TIMEOUT.value
        except Exception:  # noqa: BLE001
            log.warning("agent run %s failed", run_id, exc_info=True)
            status = AgentStatus.FAILED
            error_category = AgentErrorCategory.EXECUTION_ERROR.value

        duration_ms = int((time.monotonic() - started_at) * 1000)
        return await self._finalize_quiet(
            run_id,
            status=status,
            output=output,
            duration_ms=duration_ms,
            error_category=error_category,
        )

    async def _finalize_quiet(
        self,
        run_id: str,
        *,
        status: AgentStatus,
        output: str = "",
        duration_ms: int = 0,
        error_category: str | None = None,
    ) -> AgentRunRecord:
        record = await self._store.transition(
            run_id,
            to_status=status,
            output=output,
            duration_ms=duration_ms,
            error_category=error_category,
        )
        with contextlib.suppress(BaseException):
            await self._bus.publish(
                AgentRunFinished(
                    run_id=run_id,
                    agent_id=record.agent,
                    status=record.status.value,
                    duration_ms=duration_ms,
                    error_category=error_category or "",
                )
            )
        return record


def _to_record(row: AgentRun) -> AgentRunRecord:
    return AgentRunRecord(
        id=row.id.hex,
        agent=row.agent,
        input=row.input,
        output=row.output,
        status=AgentStatus(row.status),
        duration_ms=row.duration_ms,
        idempotency_key=row.idempotency_key,
        error_category=row.error_category,
        owner_session_id=to_hex(row.owner_session_id),
        parent_run_id=to_hex(row.parent_run_id),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
