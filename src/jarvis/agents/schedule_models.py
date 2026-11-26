"""Schedule models and missed-run policies for Phase 4.4 TaskScheduler."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ScheduleKind(StrEnum):
    ONCE = "once"
    INTERVAL = "interval"


class ScheduledJobStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    AWAITING_APPROVAL = "awaiting_approval"


class MissedPolicy(StrEnum):
    SKIP = "skip"
    RUN_ONCE = "run_once"
    REQUIRE_APPROVAL = "require_approval"


@dataclass(frozen=True)
class ScheduledJob:
    """Durable definition and execution metadata for a scheduled agent job."""

    job_id: str
    agent_id: str
    input_text: str
    kind: ScheduleKind
    next_run_at: datetime
    interval_seconds: float | None = None
    last_run_at: datetime | None = None
    status: ScheduledJobStatus = ScheduledJobStatus.ACTIVE
    missed_policy: MissedPolicy = MissedPolicy.SKIP
    timezone_name: str = "UTC"
    requires_approval: bool = False

    @classmethod
    def create(
        cls,
        *,
        agent_id: str,
        input_text: str,
        kind: ScheduleKind,
        next_run_at: datetime,
        interval_seconds: float | None = None,
        missed_policy: MissedPolicy = MissedPolicy.SKIP,
        timezone_name: str = "UTC",
        requires_approval: bool = False,
        job_id: str | None = None,
    ) -> ScheduledJob:
        if next_run_at.tzinfo is None:
            raise ValueError("next_run_at must be timezone-aware")
        if kind is ScheduleKind.INTERVAL and (
            interval_seconds is None or interval_seconds <= 0
        ):
            raise ValueError("interval_seconds must be > 0 for interval jobs")
        return cls(
            job_id=job_id or uuid.uuid4().hex,
            agent_id=agent_id,
            input_text=input_text,
            kind=kind,
            next_run_at=next_run_at,
            interval_seconds=interval_seconds,
            missed_policy=missed_policy,
            timezone_name=timezone_name,
            requires_approval=requires_approval,
        )
