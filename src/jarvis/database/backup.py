"""Database backup & restore with manifest, SHA-256 integrity, and DPAPI encryption (Phase 8.3)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any

from sqlalchemy.engine import make_url

from jarvis.core.exceptions import JarvisError
from jarvis.security.credentials import _dpapi_protect, _dpapi_unprotect

CommandRunner = Callable[[list[str], dict[str, str]], int]


class BackupError(JarvisError):
    """Base error for database backup and restore."""


class BackupIntegrityError(BackupError):
    """Raised on checksum mismatch, missing manifest, or unsafe path."""


class BackupCompatibilityError(BackupError):
    """Raised when backup schema revision does not match target."""


class BackupCancelledError(BackupError):
    """Raised when restore is cancelled or unconfirmed."""


@dataclass(frozen=True)
class BackupResult:
    """Metadata returned by ``create_backup``."""

    backup_path: Path
    manifest_path: Path
    database: str
    sha256: str
    size_bytes: int
    encrypted: bool


def _default_runner(cmd: list[str], env: dict[str, str]) -> int:
    proc = subprocess.run(cmd, env=env, check=False)  # noqa: S603
    return int(proc.returncode)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_safe_backup_path(path: Path) -> Path:
    raw = str(path)
    if not raw or "\x00" in raw or ".." in path.parts:
        raise BackupIntegrityError(f"Unsafe backup path rejected: {raw!r}")
    if not path.exists() or not path.is_file():
        raise BackupIntegrityError(f"Backup file does not exist: {path}")
    return path.resolve()


def _manifest_for(backup_path: Path) -> Path:
    name = backup_path.name
    if name.endswith(".dump.enc"):
        stem = name[: -len(".dump.enc")]
    elif name.endswith(".dump"):
        stem = name[: -len(".dump")]
    else:
        stem = backup_path.stem
    return backup_path.with_name(f"{stem}.manifest.json")


def create_backup(
    *,
    database_url: str,
    output_dir: Path,
    retention: int = 14,
    schema_revision: str = "0003_agent_runtime",
    encrypt: bool = False,
    stamp: str | None = None,
    runner: CommandRunner = _default_runner,
) -> BackupResult:
    """Run ``pg_dump``, write manifest + checksum, optionally DPAPI-encrypt, and prune."""
    url = make_url(database_url)
    db_name = url.database or "jarvis"
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = stamp or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    ext = ".dump.enc" if encrypt else ".dump"
    target = out_dir / f"{db_name}_{ts}{ext}"
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite existing backup: {target}")

    env = dict(os.environ)
    if url.password:
        env["PGPASSWORD"] = str(url.password)

    with tempfile.TemporaryDirectory() as tmp:
        raw_dump = Path(tmp) / f"{db_name}_{ts}.dump"
        cmd = [
            "pg_dump",
            "--host",
            url.host or "localhost",
            "--port",
            str(url.port or 5432),
            "--username",
            url.username or "jarvis",
            "--format",
            "custom",
            "--file",
            str(raw_dump),
            db_name,
        ]
        rc = runner(cmd, env)
        if rc != 0 or not raw_dump.exists():
            raise BackupError(f"pg_dump failed with exit code {rc}")
        raw_bytes = raw_dump.read_bytes()

    payload = _dpapi_protect(raw_bytes) if encrypt else raw_bytes
    target.write_bytes(payload)
    digest = _sha256_bytes(payload)
    manifest_path = _manifest_for(target)
    manifest = {
        "format_version": 1,
        "database": db_name,
        "schema_revision": schema_revision,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sha256": digest,
        "size_bytes": len(payload),
        "encrypted": encrypt,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Retention pruning (oldest dumps + companion manifests)
    backups = sorted(
        list(out_dir.glob(f"{db_name}_*.dump"))
        + list(out_dir.glob(f"{db_name}_*.dump.enc")),
        key=lambda p: p.name,
    )
    excess = len(backups) - max(1, retention)
    for old in backups[: max(excess, 0)]:
        old.unlink(missing_ok=True)
        _manifest_for(old).unlink(missing_ok=True)

    return BackupResult(
        backup_path=target,
        manifest_path=manifest_path,
        database=db_name,
        sha256=digest,
        size_bytes=len(payload),
        encrypted=encrypt,
    )


def restore_backup(
    *,
    backup_path: Path,
    target_database_url: str,
    confirm: bool = False,
    dry_run: bool = False,
    expected_schema_revision: str | None = "0003_agent_runtime",
    runner: CommandRunner = _default_runner,
) -> dict[str, Any]:
    """Validate manifest, checksum, schema & confirmation, then restore."""
    safe_path = _validate_safe_backup_path(Path(backup_path))
    manifest_path = _manifest_for(safe_path)
    if not manifest_path.exists():
        raise BackupIntegrityError(
            f"Missing companion manifest for backup: {manifest_path}"
        )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format_version") != 1:
        raise BackupIntegrityError("Unsupported backup manifest format_version")

    payload = safe_path.read_bytes()
    actual_sha = _sha256_bytes(payload)
    if actual_sha != manifest.get("sha256"):
        raise BackupIntegrityError("Backup checksum verification failed")

    if (
        expected_schema_revision is not None
        and manifest.get("schema_revision") != expected_schema_revision
    ):
        raise BackupCompatibilityError(
            f"Schema revision mismatch: expected {expected_schema_revision!r}, got {manifest.get('schema_revision')!r}"
        )

    if not confirm:
        raise BackupCancelledError(
            "Database restore requires explicit confirmation (--confirm)"
        )

    url = make_url(target_database_url)
    target_db = url.database or ""
    if not target_db:
        raise BackupError("Target database name is required")

    if dry_run:
        return {
            "dry_run": True,
            "restored": False,
            "target_database": target_db,
            "sha256": actual_sha,
        }

    raw_bytes = (
        _dpapi_unprotect(payload) if manifest.get("encrypted") else payload
    )
    env = dict(os.environ)
    if url.password:
        env["PGPASSWORD"] = str(url.password)

    with tempfile.TemporaryDirectory() as tmp:
        dump_file = Path(tmp) / "restore.dump"
        dump_file.write_bytes(raw_bytes)
        cmd = [
            "pg_restore",
            "--host",
            url.host or "localhost",
            "--port",
            str(url.port or 5432),
            "--username",
            url.username or "jarvis",
            "--clean",
            "--if-exists",
            "--dbname",
            target_db,
            str(dump_file),
        ]
        rc = runner(cmd, env)
        if rc != 0:
            raise BackupError(f"pg_restore failed with exit code {rc}")

    return {
        "dry_run": False,
        "restored": True,
        "target_database": target_db,
        "sha256": actual_sha,
    }
