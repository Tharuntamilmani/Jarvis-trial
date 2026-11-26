"""Tiers 4 & 5: Semantic and Episodic long-term memory stores (PostgreSQL + pgvector)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, select, text

from jarvis.conversations.models import to_uuid
from jarvis.database.connection import DatabaseService
from jarvis.database.models import Episode, Memory


@dataclass(frozen=True)
class MemoryRecord:
    """A persisted semantic/procedural/RAG memory row."""

    id: str
    kind: str
    content: str
    importance: float
    confidence: float
    source: str
    created_at: datetime


@dataclass(frozen=True)
class MemorySearchResult:
    """A retrieved memory with cosine similarity and composite rank score."""

    record: MemoryRecord
    similarity: float
    score: float


@dataclass(frozen=True)
class EpisodeRecord:
    """A persisted episodic memory row."""

    id: str
    content: str
    importance: float
    occurred_at: datetime
    created_at: datetime


def _vec_literal(embedding: list[float]) -> str:
    return "[" + ",".join(f"{float(x):.8g}" for x in embedding) + "]"


class LongTermMemoryStore:
    """PostgreSQL + pgvector persistence for semantic memories and episodes."""

    def __init__(self, db: DatabaseService) -> None:
        self._db = db

    # -- Semantic / Procedural / RAG memories (Tier 4) ------------------------

    async def add_memory(
        self,
        *,
        content: str,
        embedding: list[float],
        kind: str = "fact",
        importance: float = 0.5,
        confidence: float = 1.0,
        source: str = "user",
        dedup_threshold: float = 0.92,
    ) -> MemoryRecord:
        """Persist a memory, deduplicating by cosine similarity within ``kind``."""
        clean = content.strip()
        if not clean:
            raise ValueError("memory content cannot be empty")
        vec_str = _vec_literal(embedding)

        async with self._db.session() as session:
            # Check for near-duplicate in the same kind using cosine similarity.
            if dedup_threshold < 1.0:
                dup = await session.execute(
                    text(
                        "SELECT id, kind, content, importance, confidence, "
                        "source, created_at, "
                        "1 - (embedding <=> CAST(:vec AS vector)) AS sim "
                        "FROM memories "
                        "WHERE kind = :kind AND embedding IS NOT NULL "
                        "ORDER BY embedding <=> CAST(:vec AS vector) "
                        "LIMIT 1"
                    ),
                    {"vec": vec_str, "kind": kind},
                )
                row = dup.one_or_none()
                if row is not None and float(row.sim) >= dedup_threshold:
                    return MemoryRecord(
                        id=row.id.hex
                        if isinstance(row.id, uuid.UUID)
                        else str(row.id),
                        kind=row.kind,
                        content=row.content,
                        importance=float(row.importance),
                        confidence=float(row.confidence),
                        source=row.source,
                        created_at=row.created_at,
                    )

            record = Memory(
                id=uuid.uuid4(),
                kind=kind,
                content=clean,
                embedding=embedding,
                importance=float(importance),
                confidence=float(confidence),
                source=source,
            )
            session.add(record)
            await session.flush()
            return _to_memory_record(record)

    async def search_memories(
        self,
        query_embedding: list[float],
        *,
        limit: int = 5,
        kinds: tuple[str, ...] | None = None,
        min_similarity: float = 0.0,
    ) -> list[MemorySearchResult]:
        """Vector search with similarity + importance/recency weighting."""
        vec_str = _vec_literal(query_embedding)
        kind_filter = ""
        params: dict[str, object] = {
            "vec": vec_str,
            "lim": max(1, limit) * 3,
        }
        if kinds:
            kind_filter = "AND kind = ANY(:kinds)"
            params["kinds"] = list(kinds)

        sql = text(
            "SELECT id, kind, content, importance, confidence, source, "
            "created_at, 1 - (embedding <=> CAST(:vec AS vector)) AS sim "
            "FROM memories "
            f"WHERE embedding IS NOT NULL {kind_filter} "
            "ORDER BY embedding <=> CAST(:vec AS vector) "
            "LIMIT :lim"
        )
        async with self._db.session() as session:
            rows = (await session.execute(sql, params)).all()

        now = datetime.now(timezone.utc)
        results: list[MemorySearchResult] = []
        for row in rows:
            sim = float(row.sim)
            if sim < min_similarity:
                continue
            age_days = max(
                0.0, (now - row.created_at).total_seconds() / 86400.0
            )
            recency = 1.0 / (1.0 + age_days / 30.0)
            score = 0.7 * sim + 0.2 * float(row.importance) + 0.1 * recency
            rec = MemoryRecord(
                id=row.id.hex
                if isinstance(row.id, uuid.UUID)
                else str(row.id),
                kind=row.kind,
                content=row.content,
                importance=float(row.importance),
                confidence=float(row.confidence),
                source=row.source,
                created_at=row.created_at,
            )
            results.append(
                MemorySearchResult(record=rec, similarity=sim, score=score)
            )
        results.sort(key=lambda r: r.score, reverse=True)
        return results[: max(1, limit)]

    async def list_memories(
        self, *, limit: int = 100, kind: str | None = None
    ) -> list[MemoryRecord]:
        stmt = (
            select(
                Memory.id,
                Memory.kind,
                Memory.content,
                Memory.importance,
                Memory.confidence,
                Memory.source,
                Memory.created_at,
            )
            .order_by(Memory.created_at.desc())
            .limit(max(1, limit))
        )
        if kind is not None:
            stmt = stmt.where(Memory.kind == kind)
        async with self._db.session() as session:
            rows = (await session.execute(stmt)).all()
        return [
            MemoryRecord(
                id=row.id.hex,
                kind=row.kind,
                content=row.content,
                importance=float(row.importance),
                confidence=float(row.confidence),
                source=row.source,
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory row and its stored embedding."""
        mem_uuid = to_uuid(memory_id)
        async with self._db.session() as session:
            res = await session.execute(
                delete(Memory).where(Memory.id == mem_uuid).returning(Memory.id)
            )
            return res.scalar_one_or_none() is not None

    async def delete_by_source(self, source: str) -> int:
        """Delete all memory rows (and embeddings) matching ``source``."""
        async with self._db.session() as session:
            res = await session.execute(
                delete(Memory).where(Memory.source == source)
            )
            return int(res.rowcount or 0)

    # -- Episodic memories (Tier 5) -------------------------------------------

    async def add_episode(
        self,
        *,
        content: str,
        embedding: list[float],
        importance: float = 0.5,
        occurred_at: datetime | None = None,
    ) -> EpisodeRecord:
        clean = content.strip()
        if not clean:
            raise ValueError("episode content cannot be empty")
        ep = Episode(
            id=uuid.uuid4(),
            content=clean,
            embedding=embedding,
            importance=float(importance),
            occurred_at=occurred_at or datetime.now(timezone.utc),
        )
        async with self._db.session() as session:
            session.add(ep)
            await session.flush()
            return _to_episode_record(ep)

    async def search_episodes(
        self, query_embedding: list[float], *, limit: int = 5
    ) -> list[EpisodeRecord]:
        vec_str = _vec_literal(query_embedding)
        sql = text(
            "SELECT id, content, importance, occurred_at, created_at "
            "FROM episodes "
            "WHERE embedding IS NOT NULL "
            "ORDER BY embedding <=> CAST(:vec AS vector) "
            "LIMIT :lim"
        )
        async with self._db.session() as session:
            rows = (
                await session.execute(sql, {"vec": vec_str, "lim": max(1, limit)})
            ).all()
        return [
            EpisodeRecord(
                id=row.id.hex
                if isinstance(row.id, uuid.UUID)
                else str(row.id),
                content=row.content,
                importance=float(row.importance),
                occurred_at=row.occurred_at,
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def delete_episode(self, episode_id: str) -> bool:
        ep_uuid = to_uuid(episode_id)
        async with self._db.session() as session:
            res = await session.execute(
                delete(Episode).where(Episode.id == ep_uuid).returning(Episode.id)
            )
            return res.scalar_one_or_none() is not None


def _to_memory_record(row: Memory) -> MemoryRecord:
    return MemoryRecord(
        id=row.id.hex,
        kind=row.kind,
        content=row.content,
        importance=float(row.importance),
        confidence=float(row.confidence),
        source=row.source,
        created_at=row.created_at,
    )


def _to_episode_record(row: Episode) -> EpisodeRecord:
    return EpisodeRecord(
        id=row.id.hex,
        content=row.content,
        importance=float(row.importance),
        occurred_at=row.occurred_at,
        created_at=row.created_at,
    )
