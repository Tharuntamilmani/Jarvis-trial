"""Create the JARVIS database, extensions, and run Alembic migrations.

Usage:
    python scripts/setup_db.py [--database NAME]

Creates the target database if it does not exist (connects to the
``postgres`` maintenance database on the same server), then runs
``alembic upgrade head``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from sqlalchemy import create_engine, text  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from jarvis.config import get_settings  # noqa: E402


def database_name_from_url(url: str) -> tuple[str, str]:
    """Split a SQLAlchemy URL into (server_url, database_name)."""
    marker = url.rfind("/")
    return url[:marker], url[marker + 1 :]


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up the JARVIS database")
    parser.add_argument(
        "--database",
        default=None,
        help="Override the database name from JARVIS_DATABASE_URL",
    )
    args = parser.parse_args()

    settings = get_settings()
    server_url, database = database_name_from_url(settings.database_url)
    if args.database:
        database = args.database

    print(f"Target database: {database}")

    # Sync driver for admin operations and Alembic (asyncpg is async-only).
    admin_url = server_url.replace("+asyncpg", "+psycopg2")
    try:
        engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: cannot build admin engine ({admin_url}): {exc}")
        print("Tip: pip install psycopg2-binary, or run setup inside docker.")
        return 1

    try:
        with engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": database},
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{database}"'))
                print(f"Created database {database}")
            else:
                print(f"Database {database} already exists")
    finally:
        engine.dispose()

    alembic_cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option(
        "sqlalchemy.url", f"{admin_url}/{database}"
    )
    command.upgrade(alembic_cfg, "head")
    print("Migrations applied (head).")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
