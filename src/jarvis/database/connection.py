"""PostgreSQL connection management for JARVIS.

Async SQLAlchemy engine with pooling, ``pool_pre_ping`` connection
validation, exponential-backoff reconnection, and a health check that
publishes service status on the event bus.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from jarvis.config import Settings
from jarvis.core.event_bus import EventBus
from jarvis.core.events import ServiceStatusChanged
from jarvis.monitoring.logging import get_logger

log = get_logger(__name__)

_RECONNECT_BASE_DELAY = 1.0
_RECONNECT_MAX_DELAY = 30.0


class DatabaseService:
    """Async database access with resilience."""

    def __init__(self, settings: Settings, bus: EventBus) -> None:
        self._settings = settings
        self._bus = bus
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None

    # -- Lifecycle -------------------------------------------------------------

    def connect(self) -> None:
        """Create the engine and session factory (idempotent)."""
        if self._engine is not None:
            return
        self._engine = create_async_engine(
            self._settings.database_url,
            pool_size=self._settings.db_pool_size,
            max_overflow=self._settings.db_max_overflow,
            pool_recycle=self._settings.db_pool_recycle_seconds,
            pool_pre_ping=True,  # validate connections before use
            echo=False,
        )
        self._session_factory = async_sessionmaker(
            self._engine, expire_on_commit=False
        )
        log.info("database engine created")

    async def close(self) -> None:
        """Dispose the engine and cancel reconnection attempts."""
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconnect_task
            self._reconnect_task = None
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None
            log.info("database engine disposed")

    # -- Sessions ----------------------------------------------------------------

    @contextlib.asynccontextmanager
    async def session(self) -> Any:
        """Provide a transactional session scope."""
        if self._session_factory is None:
            self.connect()
        assert self._session_factory is not None
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    # -- Health & recovery ---------------------------------------------------------

    async def health_check(self) -> bool:
        """SELECT 1; on failure, kick off background reconnection."""
        if self._engine is None:
            self.connect()
        assert self._engine is not None
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            if self._reconnect_task is not None:
                self._reconnect_task = None  # recovered; stop the loop task
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("database health check failed: %s", exc)
            self._schedule_reconnect()
            return False

    def _schedule_reconnect(self) -> None:
        if self._reconnect_task is not None and not self._reconnect_task.done():
            return
        self._reconnect_task = asyncio.ensure_future(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        """Reconnect with exponential backoff until healthy."""
        delay = _RECONNECT_BASE_DELAY
        attempt = 0
        while True:
            attempt += 1
            await asyncio.sleep(delay)
            try:
                assert self._engine is not None
                async with self._engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                log.info("database reconnected after %d attempt(s)", attempt)
                await self._bus.publish(
                    ServiceStatusChanged(
                        service="database", status="healthy", detail="reconnected"
                    )
                )
                return
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "reconnect attempt %d failed: %s", attempt, exc
                )
                delay = min(delay * 2, _RECONNECT_MAX_DELAY)

    @property
    def is_connected(self) -> bool:
        return self._engine is not None
