"""Backup the JARVIS database with pg_dump (Phase 1 foundation).

Usage:
    python scripts/backup_db.py [--output-dir DIR]

Runs pg_dump (custom format) into the configured backup directory and
prunes old backups beyond the retention count. Scheduling via Windows Task
Scheduler is documented for Phase 8.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from sqlalchemy.engine import make_url  # noqa: E402

from jarvis.config import get_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup the JARVIS database")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    settings = get_settings()
    url = make_url(settings.database_url)
    out_dir = Path(args.output_dir) if args.output_dir else settings.backup_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    target = out_dir / f"jarvis_{stamp}.dump"

    cmd = [
        "pg_dump",
        "--host", url.host or "localhost",
        "--port", str(url.port or 5432),
        "--username", url.username or "jarvis",
        "--format", "custom",
        "--file", str(target),
        url.database or "jarvis",
    ]
    print("Running:", " ".join(cmd[:2]), "...")
    result = subprocess.run(cmd, check=False)  # noqa: S603
    if result.returncode != 0:
        print(f"pg_dump failed with exit code {result.returncode}")
        return result.returncode

    size_kb = target.stat().st_size / 1024
    print(f"Backup written: {target} ({size_kb:.1f} KB)")

    # Retention pruning
    retention = settings.backup_retention
    backups = sorted(out_dir.glob("jarvis_*.dump"))
    excess = len(backups) - retention
    for old in backups[: max(excess, 0)]:
        old.unlink()
        print(f"Pruned old backup: {old.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
