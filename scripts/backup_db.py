"""Backup the JARVIS database with manifest, checksum & retention (Phase 8.3).

Usage:
    python scripts/backup_db.py [--output-dir DIR] [--encrypt] [--retention N]
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from jarvis.config import get_settings  # noqa: E402
from jarvis.database.backup import create_backup  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup the JARVIS database")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--encrypt", action="store_true")
    parser.add_argument("--retention", type=int, default=None)
    args = parser.parse_args()

    settings = get_settings()
    out_dir = Path(args.output_dir) if args.output_dir else settings.backup_dir
    retention = args.retention if args.retention is not None else settings.backup_retention

    res = create_backup(
        database_url=settings.database_url,
        output_dir=out_dir,
        retention=retention,
        encrypt=args.encrypt,
    )
    print(f"Backup written: {res.backup_path} ({res.size_bytes / 1024:.1f} KB)")
    print(f"Manifest: {res.manifest_path} (sha256={res.sha256[:12]}...)")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
