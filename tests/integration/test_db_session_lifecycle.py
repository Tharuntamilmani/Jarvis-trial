"""DatabaseService.session() cancellation and cleanup safety."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

KEY = "k_cancellation_probe"


async def _present(db_service) -> bool:
    async with db_service.session() as session:
        row = await session.execute(
            text("SELECT 1 FROM app_settings WHERE key = :k"), {"k": KEY}
        )
        return row.scalar_one_or_none() is not None


async def test_cancellation_rolls_back_and_propagates(db_service) -> None:
    with pytest.raises(asyncio.CancelledError):
        async with db_service.session() as session:
            await session.execute(
                text("INSERT INTO app_settings (key, value) VALUES (:k, '{}'::jsonb)"),
                {"k": KEY},
            )
            raise asyncio.CancelledError()

    assert await _present(db_service) is False


async def test_ordinary_exception_rolls_back(db_service) -> None:
    with pytest.raises(RuntimeError):
        async with db_service.session() as session:
            await session.execute(
                text("INSERT INTO app_settings (key, value) VALUES (:k, '{}'::jsonb)"),
                {"k": KEY},
            )
            raise RuntimeError("boom")

    assert await _present(db_service) is False


async def test_cancel_during_a_long_statement_leaves_no_rows(
    db_service,
) -> None:
    async def writer() -> None:
        async with db_service.session() as session:
            await session.execute(
                text("INSERT INTO app_settings (key, value) VALUES (:k, '{}'::jsonb)"),
                {"k": KEY},
            )
            await session.execute(text("SELECT pg_sleep(5)"))

    task = asyncio.create_task(writer())
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await _present(db_service) is False


async def test_sessions_are_not_leaked(db_service) -> None:
    for _ in range(5):
        async with db_service.session() as session:
            await session.execute(text("SELECT 1"))

    pool = db_service._engine.pool  # noqa: SLF001
    assert pool.checkedout() == 0
