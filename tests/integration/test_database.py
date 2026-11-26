"""Database integration tests.

Run against the dedicated ``jarvis_test`` database only. The fixtures in
``tests/integration/conftest.py`` assert ``SELECT current_database()`` before
any destructive operation, so these tests can never touch the development
database. When no test database is reachable the whole module is skipped.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("migrated_database")


async def test_health_check(db_service) -> None:
    assert await db_service.health_check() is True


async def test_schema_present(db_service) -> None:
    from sqlalchemy import text

    async with db_service.session() as session:
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


async def test_vector_insert_and_select(db_service) -> None:
    """VECTOR(1024) round trip through the ORM type."""
    from sqlalchemy import text

    vec = "[" + ",".join(["0.1"] * 1024) + "]"
    async with db_service.session() as session:
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


async def test_audit_log_is_append_only(db_service) -> None:
    from sqlalchemy import text

    async with db_service.session() as session:
        await session.execute(
            text("INSERT INTO audit_log (action, result) VALUES ('probe', 'ok')")
        )
        # UPDATE and DELETE are silently ignored by DB rules.
        await session.execute(text("UPDATE audit_log SET result='hacked'"))
        count = (
            await session.execute(text("SELECT count(*) FROM audit_log"))
        ).scalar_one()
        assert count >= 1
        await session.execute(text("DELETE FROM audit_log WHERE action='probe'"))
        still = (
            await session.execute(
                text("SELECT count(*) FROM audit_log WHERE action='probe'")
            )
        ).scalar_one()
        assert still >= 1  # deletion blocked
