"""Database integration tests.

Run against a real PostgreSQL+pgvector. Skipped automatically when no
database is reachable at ``JARVIS_TEST_DATABASE_URL`` (default: the dev
container on localhost:5433).
"""

from __future__ import annotations

import os

import pytest

TEST_URL = os.environ.get(
    "JARVIS_TEST_DATABASE_URL",
    "postgresql+asyncpg://jarvis:jarvis@localhost:5433/jarvis",
)


def _database_available() -> bool:
    import asyncio

    import asyncpg

    async def probe() -> bool:
        try:
            conn = await asyncpg.connect(
                TEST_URL.replace("+asyncpg", ""), timeout=3
            )
            await conn.close()
            return True
        except Exception:  # noqa: BLE001
            return False

    try:
        return asyncio.run(asyncio.wait_for(probe(), timeout=5))
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not _database_available(), reason="no PostgreSQL available for tests"
)


@pytest.fixture()
def db():
    from jarvis.config import Settings
    from jarvis.core.event_bus import EventBus
    from jarvis.database.connection import DatabaseService

    settings = Settings(database_url=TEST_URL, _env_file=None)  # type: ignore[call-arg]
    service = DatabaseService(settings, EventBus())
    service.connect()
    return service


@pytest.mark.asyncio
async def test_health_check(db) -> None:
    assert await db.health_check() is True


@pytest.mark.asyncio
async def test_schema_present(db) -> None:
    from sqlalchemy import text

    async with db.session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public'"
                )
            )
        ).scalars()
        tables = set(rows)
    expected = {
        "users",
        "conversations",
        "messages",
        "memories",
        "episodes",
        "user_preferences",
        "agent_runs",
        "audit_log",
        "app_settings",
    }
    assert expected <= tables


@pytest.mark.asyncio
async def test_vector_insert_and_select(db) -> None:
    """VECTOR(1024) round trip through the ORM type."""
    from sqlalchemy import text

    vec = "[" + ",".join(["0.1"] * 1024) + "]"
    async with db.session() as session:
        result = await session.execute(
            text(
                "INSERT INTO memories (content, embedding, source) "
                "VALUES ('integration probe', :vec, 'test') RETURNING id"
            ),
            {"vec": vec},
        )
        memory_id = result.scalar_one()
        row = (
            await session.execute(
                text(
                    "SELECT content, embedding::text, source FROM memories "
                    "WHERE id = :id"
                ),
                {"id": memory_id},
            )
        ).one()
        await session.execute(
            text("DELETE FROM memories WHERE id = :id"), {"id": memory_id}
        )
    assert row[0] == "integration probe"
    assert row[1].startswith("[0.1")
    assert row[2] == "test"


@pytest.mark.asyncio
async def test_audit_log_is_append_only(db) -> None:
    from sqlalchemy import text

    async with db.session() as session:
        await session.execute(
            text(
                "INSERT INTO audit_log (action, result) "
                "VALUES ('probe', 'ok')"
            )
        )
        # UPDATE and DELETE are silently ignored by DB rules.
        await session.execute(text("UPDATE audit_log SET result='hacked'"))
        count = (
            await session.execute(text("SELECT count(*) FROM audit_log"))
        ).scalar_one()
        assert count >= 1
        await session.execute(
            text("DELETE FROM audit_log WHERE action='probe'")
        )
        still = (
            await session.execute(
                text("SELECT count(*) FROM audit_log WHERE action='probe'")
            )
        ).scalar_one()
        assert still >= 1  # deletion blocked
