"""PostgreSQL + pgvector integration tests for Phase 3 memory stores."""

from __future__ import annotations

from pathlib import Path

from jarvis.memory.embeddings import EmbeddingProvider
from jarvis.memory.long_term import LongTermMemoryStore
from jarvis.memory.preferences import PreferenceStore
from jarvis.memory.rag import LocalDocumentIndexer


class _DeterministicEmbedder(EmbeddingProvider):
    @property
    def dimension(self) -> int:
        return 1024

    async def embed(self, text: str) -> list[float]:
        # Produce distinct unit-ish vectors by hashing tokens into buckets.
        vec = [0.001] * 1024
        for idx, ch in enumerate(text.encode("utf-8")):
            vec[(idx + ch) % 1024] += 1.0
        return vec


def _basis(idx: int, value: float = 1.0) -> list[float]:
    vec = [0.0] * 1024
    vec[idx] = value
    return vec


async def test_semantic_memory_insert_dedup_search_and_delete(
    db_service, clean_database
) -> None:
    store = LongTermMemoryStore(db_service)

    first = await store.add_memory(
        content="User works on Python 3.12",
        embedding=_basis(0, 1.0),
        kind="fact",
        importance=0.9,
        dedup_threshold=0.92,
    )
    # Near-identical vector should deduplicate instead of creating a second row.
    dup_vec = _basis(0, 1.0)
    dup_vec[1] = 0.05
    second = await store.add_memory(
        content="User uses Python 3.12",
        embedding=dup_vec,
        kind="fact",
        importance=0.9,
        dedup_threshold=0.92,
    )
    assert second.id == first.id

    other = await store.add_memory(
        content="Project uses PostgreSQL 16",
        embedding=_basis(50, 1.0),
        kind="fact",
        importance=0.7,
        dedup_threshold=0.92,
    )
    assert other.id != first.id

    results = await store.search_memories(_basis(0, 1.0), limit=2)
    assert len(results) == 2
    assert results[0].record.id == first.id
    assert results[0].similarity > 0.99

    assert await store.delete_memory(first.id) is True
    assert await store.delete_memory(first.id) is False
    remaining = await store.list_memories()
    assert [r.id for r in remaining] == [other.id]


async def test_episodic_memory_round_trip(db_service, clean_database) -> None:
    store = LongTermMemoryStore(db_service)
    ep = await store.add_episode(
        content="Completed Stage 2.8 Gate A",
        embedding=_basis(10, 1.0),
        importance=0.8,
    )
    found = await store.search_episodes(_basis(10, 1.0), limit=3)
    assert len(found) == 1
    assert found[0].id == ep.id
    assert await store.delete_episode(ep.id) is True


async def test_user_preferences_crud(db_service, clean_database) -> None:
    prefs = PreferenceStore(db_service)
    await prefs.set_preference("theme", "dark", explicit=True)
    await prefs.set_preference("max_tokens", 2048, explicit=True)

    assert await prefs.get_preference("theme") == "dark"
    assert await prefs.get_preference("max_tokens") == 2048
    assert await prefs.list_preferences() == {
        "theme": "dark",
        "max_tokens": 2048,
    }
    assert await prefs.delete_preference("theme") is True
    assert await prefs.get_preference("theme", "light") == "light"


async def test_local_rag_index_query_and_remove(
    db_service, clean_database, tmp_path: Path
) -> None:
    doc = tmp_path / "notes.md"
    doc.write_text("JARVIS uses PostgreSQL 16 and pgvector for local RAG.", encoding="utf-8")

    store = LongTermMemoryStore(db_service)
    indexer = LocalDocumentIndexer(store, _DeterministicEmbedder())

    created = await indexer.index_file(doc)
    assert len(created) == 1

    hits = await indexer.query("PostgreSQL pgvector", limit=2)
    assert len(hits) == 1
    assert "pgvector" in hits[0]

    removed = await indexer.remove_file(doc)
    assert removed == 1
    assert await store.list_memories(kind="rag_chunk") == []
