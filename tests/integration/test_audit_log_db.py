"""PostgreSQL integration tests for Phase 7.2 append-only audit_log (jarvis_test)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from jarvis.config import Settings
from jarvis.core.event_bus import EventBus
from jarvis.security.audit import AuditService

pytestmark = pytest.mark.usefixtures("migrated_database")


async def test_audit_service_dual_writes_to_postgres_audit_log(
    db_service, tmp_path: Path
) -> None:
    settings = Settings(
        log_dir=tmp_path / "logs",
        audit_log_path=tmp_path / "logs" / "audit.log",
    )
    bus = EventBus()
    audit = AuditService(settings, bus, database=db_service)

    await audit.record_async(
        "fs.write",
        tool="fs.write",
        result="completed",
        approved_by="user",
        detail={
            "correlation_id": "corr-72",
            "call_id": "call-72",
            "actor_id": "user",
            "risk_level": "state_changing",
            "api_key": "should-be-redacted",
        },
        require_db=True,
    )

    rows = await audit.query_db(correlation_id="corr-72")
    assert len(rows) >= 1
    latest = rows[0]
    assert latest["tool"] == "fs.write"
    assert latest["result"] == "completed"
    assert latest["args"]["api_key"] == "[REDACTED]"

    async with db_service.session() as session:
        count = await session.scalar(
            text("SELECT count(*) FROM audit_log WHERE action = 'fs.write'")
        )
        assert (count or 0) >= 1
