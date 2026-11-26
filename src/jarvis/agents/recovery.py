"""Agent startup stale-run recovery and parent-child correlation (Phase 4.6)."""

from __future__ import annotations

from sqlalchemy import func, or_, select, update

from jarvis.agents.manager import _to_record
from jarvis.agents.models import (
    AgentErrorCategory,
    AgentRunRecord,
    AgentStatus,
)
from jarvis.conversations.models import to_uuid
from jarvis.database.connection import DatabaseService
from jarvis.database.models import AgentRun


class AgentRecoveryService:
    """Reconciles stale in-progress agent runs on startup without replaying."""

    def __init__(self, db: DatabaseService) -> None:
        self._db = db

    async def reconcile_stale_runs(self, *, current_session_id: str) -> int:
        """Mark prior-session ``queued``/``running`` rows as ``interrupted``."""
        session_uuid = to_uuid(current_session_id)
        async with self._db.session() as session:
            result = await session.execute(
                update(AgentRun)
                .where(
                    AgentRun.status.in_(
                        (AgentStatus.QUEUED.value, AgentStatus.RUNNING.value)
                    ),
                    or_(
                        AgentRun.owner_session_id.is_(None),
                        AgentRun.owner_session_id != session_uuid,
                    ),
                )
                .values(
                    status=AgentStatus.INTERRUPTED.value,
                    error_category=AgentErrorCategory.INTERRUPTED.value,
                    updated_at=func.now(),
                )
            )
            return int(result.rowcount or 0)

    async def find_children(self, parent_run_id: str) -> list[AgentRunRecord]:
        """Return all child agent runs correlated with ``parent_run_id``."""
        parent_uuid = to_uuid(parent_run_id)
        async with self._db.session() as session:
            rows = (
                (
                    await session.execute(
                        select(AgentRun)
                        .where(AgentRun.parent_run_id == parent_uuid)
                        .order_by(AgentRun.created_at.asc())
                    )
                )
                .scalars()
                .all()
            )
        return [_to_record(r) for r in rows]
