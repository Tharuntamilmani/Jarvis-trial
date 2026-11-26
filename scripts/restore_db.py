"""Safely restore a verified JARVIS database backup (Phase 8.3).

Usage:
    python scripts/restore_db.py --backup PATH --target-url URL [--dry-run] [--confirm]
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from jarvis.database.backup import restore_backup  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore a JARVIS database backup")
    parser.add_argument("--backup", required=True, help="Path to .dump or .dump.enc")
    parser.add_argument("--target-url", required=True, help="Explicit target DB URL")
    parser.add_argument("--expected-schema", default="0003_agent_runtime")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()

    outcome = restore_backup(
        backup_path=Path(args.backup),
        target_database_url=args.target_url,
        confirm=args.confirm,
        dry_run=args.dry_run,
        expected_schema_revision=args.expected_schema,
    )
    print(f"Restore outcome: {outcome}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
