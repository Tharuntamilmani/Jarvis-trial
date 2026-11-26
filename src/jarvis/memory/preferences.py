"""Tier 6: Persistent user preferences (PostgreSQL ``user_preferences``)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, select

from jarvis.database.connection import DatabaseService
from jarvis.database.models import User, UserPreference

_DEFAULT_USER_NAME = "local"


class PreferenceStore:
    """Stores explicit user preferences and habits in PostgreSQL."""

    def __init__(self, db: DatabaseService) -> None:
        self._db = db

    async def _ensure_default_user(self, session: Any) -> uuid.UUID:
        row = await session.execute(
            select(User.id).where(User.name == _DEFAULT_USER_NAME)
        )
        user_id = row.scalar_one_or_none()
        if user_id is not None:
            return user_id
        user = User(
            id=uuid.uuid4(),
            name=_DEFAULT_USER_NAME,
            display_name="Local User",
            preferences={},
        )
        session.add(user)
        await session.flush()
        return user.id

    async def set_preference(
        self, key: str, value: Any, *, explicit: bool = True
    ) -> None:
        clean = key.strip()
        if not clean:
            raise ValueError("preference key cannot be empty")
        async with self._db.session() as session:
            user_id = await self._ensure_default_user(session)
            existing = (
                await session.execute(
                    select(UserPreference).where(
                        UserPreference.user_id == user_id,
                        UserPreference.key == clean,
                    )
                )
            ).scalar_one_or_none()
            payload = value if isinstance(value, dict) else {"value": value}
            if existing is not None:
                existing.value = payload
                existing.explicit = explicit
            else:
                session.add(
                    UserPreference(
                        id=uuid.uuid4(),
                        user_id=user_id,
                        key=clean,
                        value=payload,
                        explicit=explicit,
                    )
                )

    async def get_preference(self, key: str, default: Any = None) -> Any:
        clean = key.strip()
        async with self._db.session() as session:
            user_id = await self._ensure_default_user(session)
            row = (
                await session.execute(
                    select(UserPreference.value).where(
                        UserPreference.user_id == user_id,
                        UserPreference.key == clean,
                    )
                )
            ).scalar_one_or_none()
        if row is None:
            return default
        if isinstance(row, dict) and set(row.keys()) == {"value"}:
            return row["value"]
        return row

    async def list_preferences(self) -> dict[str, Any]:
        async with self._db.session() as session:
            user_id = await self._ensure_default_user(session)
            rows = (
                await session.execute(
                    select(UserPreference).where(
                        UserPreference.user_id == user_id
                    )
                )
            ).scalars().all()
        result: dict[str, Any] = {}
        for row in rows:
            val = row.value
            if isinstance(val, dict) and set(val.keys()) == {"value"}:
                result[row.key] = val["value"]
            else:
                result[row.key] = val
        return result

    async def delete_preference(self, key: str) -> bool:
        clean = key.strip()
        async with self._db.session() as session:
            user_id = await self._ensure_default_user(session)
            res = await session.execute(
                delete(UserPreference).where(
                    UserPreference.user_id == user_id,
                    UserPreference.key == clean,
                )
            )
            return int(res.rowcount or 0) > 0
