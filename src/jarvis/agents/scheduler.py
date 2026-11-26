"""Local persistent TaskScheduler routing jobs through AgentManager (Phase 4.4)."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from jarvis.agents.manager import AgentManager
from jarvis.agents.schedule_models import (
    MissedPolicy,
    ScheduledJob,
    ScheduledJobStatus,
    ScheduleKind,
)
from jarvis.database.connection import DatabaseService
from jarvis.database.models import AppSetting
from jarvis.monitoring.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from jarvis.core.lifecycle import LifecycleManager

log = get_logger(__name__)

_SCHEDULER_KEY = "agents.scheduled_jobs"


class ScheduledJobStore:
    """Persists scheduled job definitions in PostgreSQL ``app_settings``."""

    def __init__(self, db: DatabaseService | None = None) -> None:
        self._db = db
        self._memory: dict[str, ScheduledJob] = {}

    async def load_all(self) -> dict[str, ScheduledJob]:
        if self._db is None:
            return dict(self._memory)
        async with self._db.session() as session:
            row = await session.execute(
                select(AppSetting.value).where(AppSetting.key == _SCHEDULER_KEY)
            )
            raw = row.scalar_one_or_none()
        if not isinstance(raw, dict):
            return {}
        jobs: dict[str, ScheduledJob] = {}
        for jid, data in raw.get("jobs", {}).items():
            if isinstance(data, dict):
                jobs[jid] = _from_dict(data)
        return jobs

    async def save_all(self, jobs: dict[str, ScheduledJob]) -> None:
        if self._db is None:
            self._memory = dict(jobs)
            return
        payload = {"jobs": {jid: _to_dict(job) for jid, job in jobs.items()}}
        stmt = (
            pg_insert(AppSetting)
            .values(key=_SCHEDULER_KEY, value=payload, updated_at=func.now())
            .on_conflict_do_update(
                index_elements=[AppSetting.key],
                set_={"value": payload, "updated_at": func.now()},
            )
        )
        async with self._db.session() as session:
            await session.execute(stmt)


class TaskScheduler:
    """Persistent, kill-switch-aware local job scheduler."""

    def __init__(
        self,
        manager: AgentManager,
        store: ScheduledJobStore | None = None,
        *,
        lifecycle: LifecycleManager | None = None,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        self._manager = manager
        self._store = store or ScheduledJobStore()
        self._lifecycle = lifecycle
        self._poll_interval = float(poll_interval_seconds)
        self._jobs: dict[str, ScheduledJob] = {}
        self._inflight: set[str] = set()
        self._loop_task: asyncio.Task[None] | None = None
        self._closing = False

    async def load(self) -> None:
        self._jobs = await self._store.load_all()

    def list_jobs(self) -> tuple[ScheduledJob, ...]:
        return tuple(self._jobs.values())

    async def add_job(self, job: ScheduledJob) -> ScheduledJob:
        self._jobs[job.job_id] = job
        await self._store.save_all(self._jobs)
        return job

    async def pause_job(self, job_id: str) -> ScheduledJob:
        job = self._jobs[job_id]
        updated = dataclasses.replace(job, status=ScheduledJobStatus.PAUSED)
        self._jobs[job_id] = updated
        await self._store.save_all(self._jobs)
        return updated

    async def resume_job(self, job_id: str) -> ScheduledJob:
        job = self._jobs[job_id]
        updated = dataclasses.replace(job, status=ScheduledJobStatus.ACTIVE)
        self._jobs[job_id] = updated
        await self._store.save_all(self._jobs)
        return updated

    async def cancel_job(self, job_id: str) -> ScheduledJob:
        job = self._jobs[job_id]
        updated = dataclasses.replace(job, status=ScheduledJobStatus.CANCELLED)
        self._jobs[job_id] = updated
        await self._store.save_all(self._jobs)
        return updated

    def start(self) -> None:
        if self._closing:
            return
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.create_task(self._run_loop())

    async def close(self) -> None:
        self._closing = True
        task = self._loop_task
        self._loop_task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await asyncio.gather(task, return_exceptions=True)

    async def _run_loop(self) -> None:
        while not self._closing:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.warning("scheduler tick failed", exc_info=True)
            await asyncio.sleep(self._poll_interval)

    async def tick(self, *, now: datetime | None = None) -> list[str]:
        """Evaluate due jobs deterministically; returns submitted ``run_id``s."""
        if self._closing:
            return []
        if self._lifecycle is not None and self._lifecycle.kill_switch_active:
            return []

        current = now or datetime.now(timezone.utc)
        submitted: list[str] = []
        changed = False

        for jid, job in list(self._jobs.items()):
            if job.status is not ScheduledJobStatus.ACTIVE:
                continue
            if jid in self._inflight:
                continue
            if job.next_run_at > current:
                continue

            missed = (current - job.next_run_at) > timedelta(
                seconds=max(1.0, (job.interval_seconds or 60.0))
            )
            if missed and job.missed_policy is MissedPolicy.SKIP:
                if job.kind is ScheduleKind.INTERVAL and job.interval_seconds:
                    next_at = _advance_past(
                        job.next_run_at, current, job.interval_seconds
                    )
                    self._jobs[jid] = dataclasses.replace(
                        job, next_run_at=next_at
                    )
                else:
                    self._jobs[jid] = dataclasses.replace(
                        job, status=ScheduledJobStatus.COMPLETED
                    )
                changed = True
                continue

            if (
                (missed and job.missed_policy is MissedPolicy.REQUIRE_APPROVAL)
                or job.requires_approval
            ):
                self._jobs[jid] = dataclasses.replace(
                    job, status=ScheduledJobStatus.AWAITING_APPROVAL
                )
                changed = True
                continue

            # Trigger once with duplicate-trigger idempotency key.
            epoch = int(job.next_run_at.timestamp())
            idem = f"sched:{jid}:{epoch}"
            self._inflight.add(jid)
            try:
                run_id = await self._manager.submit(
                    job.agent_id,
                    job.input_text,
                    idempotency_key=idem,
                )
                submitted.append(run_id)
            finally:
                self._inflight.discard(jid)

            if job.kind is ScheduleKind.INTERVAL and job.interval_seconds:
                next_at = _advance_past(
                    job.next_run_at, current, job.interval_seconds
                )
                self._jobs[jid] = dataclasses.replace(
                    job, last_run_at=current, next_run_at=next_at
                )
            else:
                self._jobs[jid] = dataclasses.replace(
                    job,
                    last_run_at=current,
                    status=ScheduledJobStatus.COMPLETED,
                )
            changed = True

        if changed:
            await self._store.save_all(self._jobs)
        return submitted


def _advance_past(
    anchor: datetime, current: datetime, interval_seconds: float
) -> datetime:
    step = timedelta(seconds=interval_seconds)
    nxt = anchor + step
    while nxt <= current:
        nxt += step
    return nxt


def _to_dict(job: ScheduledJob) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "agent_id": job.agent_id,
        "input_text": job.input_text,
        "kind": job.kind.value,
        "next_run_at": job.next_run_at.isoformat(),
        "interval_seconds": job.interval_seconds,
        "last_run_at": job.last_run_at.isoformat() if job.last_run_at else None,
        "status": job.status.value,
        "missed_policy": job.missed_policy.value,
        "timezone_name": job.timezone_name,
        "requires_approval": job.requires_approval,
    }


def _from_dict(data: dict[str, Any]) -> ScheduledJob:
    return ScheduledJob(
        job_id=str(data["job_id"]),
        agent_id=str(data["agent_id"]),
        input_text=str(data["input_text"]),
        kind=ScheduleKind(data["kind"]),
        next_run_at=datetime.fromisoformat(data["next_run_at"]),
        interval_seconds=data.get("interval_seconds"),
        last_run_at=(
            datetime.fromisoformat(data["last_run_at"])
            if data.get("last_run_at")
            else None
        ),
        status=ScheduledJobStatus(data["status"]),
        missed_policy=MissedPolicy(data.get("missed_policy", "skip")),
        timezone_name=str(data.get("timezone_name", "UTC")),
        requires_approval=bool(data.get("requires_approval", False)),
    )
