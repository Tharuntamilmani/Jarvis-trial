"""PostgreSQL integration-test fixtures with hard identity guards.

Every destructive setup/cleanup operation asserts ``SELECT current_database()``
is exactly ``jarvis_test``. The development database (``jarvis``) is never a
valid target. If the connection cannot be verified, the operation aborts
instead of proceeding.

The test URL is deliberately configured here rather than inherited from
``.env``: ``.env`` points at the development database.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEST_DATABASE_NAME = "jarvis_test"
DEFAULT_TEST_URL = "postgresql+asyncpg://jarvis:jarvis@localhost:5433/jarvis_test"


def test_database_url() -> str:
    """Test-only URL; never falls back to the development database."""
    return os.environ.get("JARVIS_TEST_DATABASE_URL", DEFAULT_TEST_URL)


def sa_sync_url(url: str) -> str:
    """SQLAlchemy synchronous URL (used by Alembic)."""
    return url.replace("+asyncpg", "+psycopg2")


def libpq_url(url: str) -> str:
    """Plain libpq DSN (used by psycopg2)."""
    return url.replace("+asyncpg", "")


def connect(url: str) -> Any:
    import psycopg2

    return psycopg2.connect(libpq_url(url), connect_timeout=3)


def database_available(url: str) -> bool:
    try:
        connection = connect(url)
    except Exception:  # noqa: BLE001 - absence means "skip"
        return False
    connection.close()
    return True


def assert_test_database(connection: Any) -> str:
    """Abort unless this connection is the dedicated test database."""
    cursor = connection.cursor()
    cursor.execute("SELECT current_database()")
    name = cursor.fetchone()[0]
    if name != TEST_DATABASE_NAME:
        raise RuntimeError(
            "refusing a destructive test operation: connected to "
            f"{name!r}, expected {TEST_DATABASE_NAME!r}"
        )
    return name


def alembic_config(url: str) -> Any:
    from alembic.config import Config

    # No ini file: ``env.py`` only calls ``fileConfig`` when a config file is
    # present, and reconfiguring logging would disable the loggers that other
    # tests assert against.
    config = Config()
    config.set_main_option(
        "script_location", str(PROJECT_ROOT / "database" / "migrations")
    )
    config.set_main_option("sqlalchemy.url", sa_sync_url(url))
    return config


# -- Fixtures -----------------------------------------------------------------


@pytest.fixture(scope="session")
def db_url() -> str:
    url = test_database_url()
    if not database_available(url):
        pytest.skip(
            f"PostgreSQL test database is not reachable at {url.rsplit('@', 1)[-1]}"
        )
    return url


@pytest.fixture(scope="session")
def migrated_database(db_url: str) -> str:
    """Migrate ``jarvis_test`` to head once per session (identity guarded)."""
    from alembic import command

    connection = connect(db_url)
    try:
        assert_test_database(connection)
    finally:
        connection.close()
    command.upgrade(alembic_config(db_url), "head")
    return db_url


@pytest.fixture()
def clean_database(migrated_database: str) -> str:
    """Truncate conversation state before a test (identity guarded)."""
    connection = connect(migrated_database)
    try:
        connection.autocommit = True
        assert_test_database(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                "TRUNCATE messages, conversations, app_settings, "
                "memories, episodes, user_preferences, users, agent_runs CASCADE"
            )
    finally:
        connection.close()
    return migrated_database


@pytest.fixture()
def baseline_database(db_url: str):
    """Provide the schema at ``0001_baseline`` for populated-migration tests.

    Destructive, therefore identity-guarded, and always restored to head so
    later tests see the current schema.
    """
    from alembic import command

    connection = connect(db_url)
    try:
        assert_test_database(connection)
    finally:
        connection.close()
    config = alembic_config(db_url)
    command.downgrade(config, "base")
    command.upgrade(config, "0001_baseline")
    try:
        yield db_url
    finally:
        command.upgrade(config, "head")


@pytest.fixture()
def db_settings(clean_database: str, tmp_path: Path):
    from jarvis.config import Settings

    return Settings(
        database_url=clean_database,
        log_dir=tmp_path / "logs",
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture()
async def db_service(db_settings):
    from jarvis.core.event_bus import EventBus
    from jarvis.database.connection import DatabaseService

    service = DatabaseService(db_settings, EventBus())
    service.connect()
    try:
        yield service
    finally:
        await service.close()


@pytest.fixture()
async def conversations(db_service, db_settings):
    from jarvis.conversations.service import ConversationService

    return ConversationService.from_database(
        db_service, db_settings, session_id="a" * 32
    )
