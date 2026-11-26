"""Migration 0002 against a populated baseline database."""

from __future__ import annotations

import psycopg2
import pytest
from alembic import command
from alembic.config import Config
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONV = "11111111-1111-1111-1111-111111111111"


def _connect(url: str):
    return psycopg2.connect(url.replace("+asyncpg", ""), connect_timeout=3)


def _config(url: str) -> Config:
    config = Config()  # no ini file -> env.py skips fileConfig()
    config.set_main_option(
        "script_location", str(PROJECT_ROOT / "database" / "migrations")
    )
    config.set_main_option("sqlalchemy.url", url.replace("+asyncpg", "+psycopg2"))
    return config


def test_populated_database_is_backfilled(baseline_database: str) -> None:
    connection = _connect(baseline_database)
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO conversations (id, title, mode, created_at) "
            "VALUES (%s, 'Legacy', 'workspace', "
            "'2026-01-01 08:00:00+00')",
            (CONV,),
        )
        # Inserted out of chronological order on purpose.
        cursor.execute(
            "INSERT INTO messages (id, conversation_id, role, content, "
            "created_at) VALUES "
            "('aaaaaaaa-0000-0000-0000-000000000002', %s, 'assistant', "
            "'second', '2026-01-01 10:00:00+00')",
            (CONV,),
        )
        cursor.execute(
            "INSERT INTO messages (id, conversation_id, role, content, "
            "created_at) VALUES "
            "('aaaaaaaa-0000-0000-0000-000000000001', %s, 'user', "
            "'first', '2026-01-01 09:00:00+00')",
            (CONV,),
        )
    connection.close()

    command.upgrade(_config(baseline_database), "head")

    connection = _connect(baseline_database)
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, seq, status, content_version, request_id, provider, "
            "privacy, owner_session_id, retry_of_request_id, "
            "updated_at = created_at FROM messages "
            "WHERE conversation_id = %s ORDER BY seq",
            (CONV,),
        )
        rows = cursor.fetchall()
        cursor.execute(
            "SELECT updated_at = created_at FROM conversations WHERE id = %s",
            (CONV,),
        )
        conversation_backfilled = cursor.fetchone()[0]
    connection.close()

    assert [row[1] for row in rows] == [1, 2]
    assert rows[0][0].endswith("0001")
    assert rows[1][0].endswith("0002")
    for row in rows:
        assert row[2] == "completed"
        assert row[3] == 0
        assert row[4] is None
        assert row[5] is None
        assert row[6] is None
        assert row[7] is None
        assert row[8] is None
        assert row[9] is True
    assert conversation_backfilled is True


def _seed_conversation(url: str) -> str:
    import uuid

    conversation_id = str(uuid.uuid4())
    connection = _connect(url)
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO conversations (id, title) VALUES (%s, 'c')",
            (conversation_id,),
        )
    connection.close()
    return conversation_id


def test_unknown_role_is_rejected(migrated_database: str) -> None:
    conversation_id = _seed_conversation(migrated_database)
    connection = _connect(migrated_database)
    connection.autocommit = True
    try:
        with pytest.raises(psycopg2.errors.CheckViolation):
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO messages (conversation_id, role, content, "
                    "seq, status) VALUES (%s, 'robot', 'x', 1, 'completed')",
                    (conversation_id,),
                )
    finally:
        connection.close()


def test_user_message_must_be_completed(migrated_database: str) -> None:
    conversation_id = _seed_conversation(migrated_database)
    connection = _connect(migrated_database)
    connection.autocommit = True
    try:
        with pytest.raises(psycopg2.errors.CheckViolation):
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO messages (conversation_id, role, content, "
                    "seq, status) VALUES (%s, 'user', 'x', 1, 'pending')",
                    (conversation_id,),
                )
    finally:
        connection.close()


def test_duplicate_sequence_is_rejected(migrated_database: str) -> None:
    conversation_id = _seed_conversation(migrated_database)
    connection = _connect(migrated_database)
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO messages (conversation_id, role, content, seq) "
                "VALUES (%s, 'assistant', 'a', 1)",
                (conversation_id,),
            )
        with pytest.raises(psycopg2.errors.UniqueViolation):
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO messages (conversation_id, role, content, "
                    "seq) VALUES (%s, 'assistant', 'b', 1)",
                    (conversation_id,),
                )
    finally:
        connection.close()
