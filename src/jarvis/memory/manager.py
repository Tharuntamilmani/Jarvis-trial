"""MemoryManager: coordinates all six memory tiers with zero auto-permanence."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from jarvis.config import Settings
from jarvis.database.connection import DatabaseService
from jarvis.memory.embeddings import EmbeddingProvider, OllamaEmbeddingService
from jarvis.memory.long_term import (
    EpisodeRecord,
    LongTermMemoryStore,
    MemoryRecord,
    MemorySearchResult,
)
from jarvis.memory.preferences import PreferenceStore
from jarvis.memory.rag import LocalDocumentIndexer
from jarvis.memory.short_term import ShortTermMemory
from jarvis.memory.working_memory import WorkingMemory


@dataclass(frozen=True)
class MemoryProposal:
    """A candidate memory awaiting explicit user approval before persistence."""

    id: str
    content: str
    kind: str = "fact"
    confidence: float = 0.7
    source: str = "consolidation"
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class MemoryManager:
    """Unified facade over Working, Short-Term, Long-Term, Episodic, Preferences, and RAG."""

    def __init__(
        self,
        *,
        store: LongTermMemoryStore,
        preferences: PreferenceStore,
        embedder: EmbeddingProvider,
        settings: Settings,
    ) -> None:
        self.working = WorkingMemory()
        self.short_term = ShortTermMemory()
        self.store = store
        self.preferences = preferences
        self.embedder = embedder
        self.rag = LocalDocumentIndexer(store, embedder)
        self._dedup_threshold = float(settings.memory_dedup_threshold)
        self._proposals: dict[str, MemoryProposal] = {}

    @classmethod
    def from_database(
        cls,
        db: DatabaseService,
        settings: Settings,
        *,
        embedder: EmbeddingProvider | None = None,
    ) -> MemoryManager:
        return cls(
            store=LongTermMemoryStore(db),
            preferences=PreferenceStore(db),
            embedder=embedder or OllamaEmbeddingService(settings),
            settings=settings,
        )

    async def close(self) -> None:
        close_fn = getattr(self.embedder, "close", None)
        if close_fn is not None:
            await close_fn()

    # -- Consolidation proposals (no automatic permanence) --------------------

    def propose_memory(
        self,
        content: str,
        *,
        kind: str = "fact",
        confidence: float = 0.7,
        source: str = "consolidation",
    ) -> MemoryProposal:
        """Queue a candidate memory for explicit user review (never auto-saved)."""
        clean = content.strip()
        if not clean:
            raise ValueError("proposed memory content cannot be empty")
        proposal = MemoryProposal(
            id=uuid.uuid4().hex,
            content=clean,
            kind=kind,
            confidence=float(confidence),
            source=source,
        )
        self._proposals[proposal.id] = proposal
        return proposal

    def pending_proposals(self) -> list[MemoryProposal]:
        return list(self._proposals.values())

    async def approve_proposal(
        self, proposal_id: str, *, importance: float = 0.7
    ) -> MemoryRecord:
        """Explicitly promote an approved proposal into PostgreSQL + pgvector."""
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise KeyError(f"unknown memory proposal: {proposal_id}")
        vec = await self.embedder.embed(proposal.content)
        record = await self.store.add_memory(
            content=proposal.content,
            embedding=vec,
            kind=proposal.kind,
            importance=importance,
            confidence=proposal.confidence,
            source=proposal.source,
            dedup_threshold=self._dedup_threshold,
        )
        self._proposals.pop(proposal_id, None)
        return record

    def reject_proposal(self, proposal_id: str) -> bool:
        return self._proposals.pop(proposal_id, None) is not None

    # -- Explicit user Remember / Forget --------------------------------------

    async def remember(
        self,
        content: str,
        *,
        kind: str = "fact",
        importance: float = 0.8,
        source: str = "user",
    ) -> MemoryRecord:
        """Explicitly store a user-pinned memory."""
        vec = await self.embedder.embed(content)
        return await self.store.add_memory(
            content=content,
            embedding=vec,
            kind=kind,
            importance=importance,
            confidence=1.0,
            source=source,
            dedup_threshold=self._dedup_threshold,
        )

    async def remember_episode(
        self,
        content: str,
        *,
        importance: float = 0.6,
        occurred_at: datetime | None = None,
    ) -> EpisodeRecord:
        vec = await self.embedder.embed(content)
        return await self.store.add_episode(
            content=content,
            embedding=vec,
            importance=importance,
            occurred_at=occurred_at,
        )

    async def forget(self, memory_id: str) -> bool:
        return await self.store.delete_memory(memory_id)

    async def forget_episode(self, episode_id: str) -> bool:
        return await self.store.delete_episode(episode_id)

    # -- Local-only retrieval -------------------------------------------------

    async def search(
        self, query: str, *, limit: int = 5
    ) -> list[MemorySearchResult]:
        vec = await self.embedder.embed(query)
        return await self.store.search_memories(vec, limit=limit)

    async def build_local_context(
        self, query: str, *, limit: int = 5
    ) -> str:
        """Build local-only memory + RAG context (never transmitted to Groq)."""
        results = await self.search(query, limit=limit)
        if not results:
            return ""
        lines = [f"- [{r.record.kind}] {r.record.content}" for r in results]
        return "\n".join(lines)
