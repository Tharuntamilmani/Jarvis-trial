"""Unit tests for Phase 8.5 Single-Instance, Safe-Mode & Crash Recovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.config import Settings
from jarvis.core.engine import CoreEngine
from jarvis.core.event_bus import EventBus
from jarvis.core.recovery import CrashRecoveryManager
from jarvis.core.single_instance import SingleInstanceLock
from jarvis.security.audit import AuditService


def test_single_instance_mutex_prevents_duplicates_and_releases() -> None:
    name = "Local\\JARVIS_TEST_MUTEX_85"
    lock1 = SingleInstanceLock(name)
    lock2 = SingleInstanceLock(name)
    try:
        assert lock1.acquire() is True
        assert lock2.acquire() is False
        lock1.release()
        # After release, lock2 can acquire
        assert lock2.acquire() is True
    finally:
        lock1.release()
        lock2.release()


@pytest.mark.asyncio
async def test_crash_recovery_reconciles_without_replaying_destructive_tools(
    tmp_path: Path,
) -> None:
    bus = EventBus()
    settings = Settings(
        log_dir=tmp_path / "logs",
        audit_log_path=tmp_path / "logs" / "audit.log",
    )
    audit = AuditService(settings, bus)

    # Simulate crash after 'started' without terminal outcome
    await audit.record_async(
        "fs.delete",
        result="started",
        detail={
            "call_id": "call-destr-1",
            "correlation_id": "corr-1",
            "risk_level": "destructive",
        },
    )
    await audit.record_async(
        "fs.read",
        result="started",
        detail={
            "call_id": "call-read-1",
            "correlation_id": "corr-2",
            "risk_level": "read_only",
        },
    )
    await audit.record_async(
        "fs.mkdir",
        result="started",
        detail={
            "call_id": "call-ok-1",
            "correlation_id": "corr-3",
            "risk_level": "state_changing",
        },
    )
    await audit.record_async(
        "fs.mkdir",
        result="completed",
        detail={"call_id": "call-ok-1", "correlation_id": "corr-3"},
    )

    replayed: list[str] = []
    mgr = CrashRecoveryManager(audit=audit)
    report = await mgr.reconcile()

    assert report["replayed_count"] == 0
    assert replayed == []
    assert "call-destr-1" in report["interrupted_non_replayable"]
    assert "call-read-1" in report["interrupted_read_only"]
    assert "call-ok-1" not in report["interrupted_non_replayable"]

    # Second reconcile is idempotent (already marked interrupted in audit)
    report2 = await mgr.reconcile()
    assert report2["interrupted_non_replayable"] == []


def test_core_engine_safe_mode_disables_optional_subsystems(tmp_path: Path) -> None:
    settings = Settings(
        log_dir=tmp_path / "logs",
        audit_log_path=tmp_path / "logs" / "audit.log",
    )
    engine = CoreEngine(settings, safe_mode=True)
    engine.assemble()
    assert engine.safe_mode is True
    assert engine.registry.try_get("voice") is None
    assert "voice" not in engine.registry.names
    assert "vision" not in engine.registry.names
    assert "agents" not in engine.registry.names
