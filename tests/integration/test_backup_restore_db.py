"""PostgreSQL backup & restore round-trip test strictly against jarvis_test."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest
from sqlalchemy.engine import make_url

from jarvis.database.backup import create_backup, restore_backup

pytestmark = pytest.mark.usefixtures("migrated_database")

_TEST_DB_URL = os.environ.get(
    "JARVIS_TEST_DATABASE_URL",
    "postgresql+asyncpg://jarvis:jarvis@localhost:5433/jarvis_test",
)


def _docker_pg_runner(cmd: list[str], env: dict[str, str]) -> int:
    """Run pg_dump/pg_restore via jarvis-db container if server version > host."""
    del env
    tool = cmd[0]
    if tool == "pg_dump":
        file_idx = cmd.index("--file") + 1
        host_file = Path(cmd[file_idx])
        db_name = cmd[-1]
        assert db_name == "jarvis_test"
        proc = subprocess.run(
            [
                "docker",
                "exec",
                "jarvis-db",
                "pg_dump",
                "-U",
                "jarvis",
                "--format",
                "custom",
                db_name,
            ],
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            return proc.returncode
        host_file.write_bytes(proc.stdout)
        return 0

    if tool == "pg_restore":
        host_file = Path(cmd[-1])
        db_name = cmd[cmd.index("--dbname") + 1]
        assert db_name == "jarvis_test"
        proc = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                "jarvis-db",
                "pg_restore",
                "-U",
                "jarvis",
                "--clean",
                "--if-exists",
                "--dbname",
                db_name,
            ],
            input=host_file.read_bytes(),
            capture_output=True,
            check=False,
        )
        return proc.returncode
    return 1


async def test_backup_and_restore_round_trip_jarvis_test_only(
    db_service, tmp_path: Path
) -> None:
    url = make_url(_TEST_DB_URL)
    assert url.database == "jarvis_test"

    backup_dir = tmp_path / "backups"
    result = create_backup(
        database_url=_TEST_DB_URL,
        output_dir=backup_dir,
        retention=5,
        schema_revision="0003_agent_runtime",
        encrypt=False,
        runner=_docker_pg_runner,
    )
    assert result.backup_path.exists()
    assert result.size_bytes > 0

    restored = restore_backup(
        backup_path=result.backup_path,
        target_database_url=_TEST_DB_URL,
        confirm=True,
        expected_schema_revision="0003_agent_runtime",
        runner=_docker_pg_runner,
    )
    assert restored["target_database"] == "jarvis_test"
    assert restored["restored"] is True
    assert await db_service.health_check() is True
