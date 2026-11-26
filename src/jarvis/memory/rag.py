"""Local-only document & folder RAG indexing into pgvector (Phase 3.5)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from jarvis.memory.embeddings import EmbeddingProvider
from jarvis.memory.long_term import LongTermMemoryStore, MemoryRecord

_TEXT_SUFFIXES = frozenset({".md", ".txt", ".py", ".rst", ".json", ".toml"})


def _source_key(path: Path) -> str:
    """Bounded (<=30 char) source key for ``memories.source``."""
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:24]
    return f"rag:{digest}"


def chunk_text(
    text: str, *, chunk_size: int = 800, overlap: int = 120
) -> list[str]:
    """Deterministically split text into overlapping character chunks."""
    clean = text.strip()
    if not clean:
        return []
    if chunk_size <= overlap:
        raise ValueError("chunk_size must exceed overlap")
    chunks: list[str] = []
    start = 0
    step = chunk_size - overlap
    while start < len(clean):
        piece = clean[start : start + chunk_size].strip()
        if piece:
            chunks.append(piece)
        start += step
    return chunks


class LocalDocumentIndexer:
    """Opt-in local file indexer for private pgvector retrieval."""

    def __init__(
        self,
        store: LongTermMemoryStore,
        embedder: EmbeddingProvider,
    ) -> None:
        self._store = store
        self._embedder = embedder

    async def index_file(
        self, path: Path, *, chunk_size: int = 800, overlap: int = 120
    ) -> list[MemoryRecord]:
        """Read, chunk, embed, and store a local text/markdown/code file."""
        resolved = path.resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"not a file: {resolved}")
        if resolved.suffix.lower() not in _TEXT_SUFFIXES:
            raise ValueError(f"unsupported file type: {resolved.suffix}")

        content = resolved.read_text(encoding="utf-8", errors="replace")
        source_key = _source_key(resolved)
        # Replace any prior chunks for this file so deletions/updates stay clean.
        await self._store.delete_by_source(source_key)

        created: list[MemoryRecord] = []
        for chunk in chunk_text(
            content, chunk_size=chunk_size, overlap=overlap
        ):
            vec = await self._embedder.embed(chunk)
            rec = await self._store.add_memory(
                content=f"[{resolved.name}] {chunk}",
                embedding=vec,
                kind="rag_chunk",
                importance=0.6,
                confidence=1.0,
                source=source_key,
                dedup_threshold=0.99,
            )
            created.append(rec)
        return created

    async def remove_file(self, path: Path) -> int:
        """Remove all indexed chunks and embeddings for ``path``."""
        return await self._store.delete_by_source(_source_key(path))

    async def query(self, question: str, *, limit: int = 4) -> list[str]:
        """Retrieve relevant local document chunks (never sent to Groq)."""
        vec = await self._embedder.embed(question)
        results = await self._store.search_memories(
            vec, limit=limit, kinds=("rag_chunk",)
        )
        return [r.record.content for r in results]
