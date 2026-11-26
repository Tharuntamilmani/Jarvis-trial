"""Unit tests for Phase 8.3 database backup, manifest, encryption & restore safeguards."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.database.backup import (
    BackupCancelledError,
    BackupCompatibilityError,
    BackupIntegrityError,
    create_backup,
    restore_backup,
)


def _fake_dump_runner(cmd: list[str], env: dict[str, str]) -> int:
    del env
    if "--file" in cmd:
        out_path = Path(cmd[cmd.index("--file") + 1])
        out_path.write_bytes(b"PGDMP_FAKE_ARCHIVE_BYTES")
    return 0


def test_backup_manifest_encryption_and_retention_pruning(tmp_path: Path) -> None:
    out_dir = tmp_path / "backups"
    # Create 2 encrypted backups with retention=1 -> oldest pruned
    b1 = create_backup(
        database_url="postgresql+asyncpg://jarvis:jarvis@localhost:5433/jarvis_test",
        output_dir=out_dir,
        retention=2,
        schema_revision="0002",
        encrypt=True,
        stamp="20261126_010000",
        runner=_fake_dump_runner,
    )
    assert b1.backup_path.exists()
    assert b1.manifest_path.exists()
    assert b1.encrypted is True
    # Ciphertext is DPAPI-protected, not raw PGDMP header
    assert not b1.backup_path.read_bytes().startswith(b"PGDMP")

    b2 = create_backup(
        database_url="postgresql+asyncpg://jarvis:jarvis@localhost:5433/jarvis_test",
        output_dir=out_dir,
        retention=1,
        schema_revision="0002",
        encrypt=True,
        stamp="20261126_020000",
        runner=_fake_dump_runner,
    )
    assert b2.backup_path.exists()
    # b1 and its manifest were pruned by retention=1
    assert not b1.backup_path.exists()
    assert not b1.manifest_path.exists()


def test_restore_rejects_corrupted_incompatible_unconfirmed_and_traversal(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "backups"
    b = create_backup(
        database_url="postgresql+asyncpg://jarvis:jarvis@localhost:5433/jarvis_test",
        output_dir=out_dir,
        retention=5,
        schema_revision="0003_agent_runtime",
        encrypt=False,
        stamp="20261126_030000",
        runner=_fake_dump_runner,
    )

    # Unconfirmed restore cancelled
    with pytest.raises(BackupCancelledError):
        restore_backup(
            backup_path=b.backup_path,
            target_database_url="postgresql+asyncpg://jarvis:jarvis@localhost:5433/jarvis_test",
            confirm=False,
            runner=_fake_dump_runner,
        )

    # Incompatible schema rejected
    with pytest.raises(BackupCompatibilityError):
        restore_backup(
            backup_path=b.backup_path,
            target_database_url="postgresql+asyncpg://jarvis:jarvis@localhost:5433/jarvis_test",
            confirm=True,
            expected_schema_revision="0099",
            runner=_fake_dump_runner,
        )

    # Path traversal rejected
    with pytest.raises(BackupIntegrityError):
        restore_backup(
            backup_path=Path("../evil.dump"),
            target_database_url="postgresql+asyncpg://jarvis:jarvis@localhost:5433/jarvis_test",
            confirm=True,
            runner=_fake_dump_runner,
        )

    # Dry-run succeeds on valid backup
    dry = restore_backup(
        backup_path=b.backup_path,
        target_database_url="postgresql+asyncpg://jarvis:jarvis@localhost:5433/jarvis_test",
        confirm=True,
        dry_run=True,
        expected_schema_revision="0003_agent_runtime",
        runner=_fake_dump_runner,
    )
    assert dry["dry_run"] is True
    assert dry["target_database"] == "jarvis_test"

    # Corrupted backup payload rejected via SHA-256 mismatch
    b.backup_path.write_bytes(b"CORRUPTED_BYTES")
    with pytest.raises(BackupIntegrityError, match="checksum"):
        restore_backup(
            backup_path=b.backup_path,
            target_database_url="postgresql+asyncpg://jarvis:jarvis@localhost:5433/jarvis_test",
            confirm=True,
            runner=_fake_dump_runner,
        )

    # Manifest check
    manifest = json.loads(b.manifest_path.read_text(encoding="utf-8"))
    assert manifest["database"] == "jarvis_test"
