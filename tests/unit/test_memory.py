"""Unit tests for Phase 3 six-tier memory subsystem (no Qt, mocked embeddings)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

from jarvis.config import Settings
from jarvis.memory import (
    EmbeddingError,
    LocalDocumentIndexer,
    MemoryManager,
    OllamaEmbeddingService,
    ShortTermMemory,
    WorkingMemory,
    chunk_text,
)


def _settings(tmp_path: Path, dim: int = 64) -> Settings:
    return Settings(
        log_dir=tmp_path / "logs",
        embedding_dim=dim,
        _env_file=None,  # type: ignore[call-arg]
    )


class _FakeEmbedder:
    def __init__(self, dim: int = 64) -> None:
        self._dim = dim
        self.calls: list[str] = []

    @property
    def dimension(self) -> int:
        return self._dim

    async def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        base = float(sum(ord(c) for c in text) % 10 + 1)
        return [base] * self._dim


class _FakeStore:
    def __init__(self) -> None:
        self.added: list[dict] = []
        self.deleted_sources: list[str] = []
        self.deleted_ids: list[str] = []

    async def add_memory(self, **kwargs):
        from datetime import datetime, timezone
        from jarvis.memory.long_term import MemoryRecord

        self.added.append(kwargs)
        return MemoryRecord(
            id=f"{len(self.added):032x}",
            kind=kwargs.get("kind", "fact"),
            content=kwargs["content"],
            importance=kwargs.get("importance", 0.5),
            confidence=kwargs.get("confidence", 1.0),
            source=kwargs.get("source", "user"),
            created_at=datetime.now(timezone.utc),
        )

    async def search_memories(self, query_embedding, *, limit=5, kinds=None, min_similarity=0.0):
        from jarvis.memory.long_term import MemorySearchResult

        items = [
            MemorySearchResult(
                record=await self.add_memory(content="local fact", embedding=query_embedding),
                similarity=0.9,
                score=0.9,
            )
        ]
        return items[:limit]

    async def delete_memory(self, memory_id: str) -> bool:
        self.deleted_ids.append(memory_id)
        return True

    async def delete_by_source(self, source: str) -> int:
        self.deleted_sources.append(source)
        return 1


def test_memory_package_does_not_import_pyside6() -> None:
    assert "PySide6" not in sys.modules or all(
        not name.startswith("jarvis.memory")
        for name in sys.modules
        if "PySide6" in name
    )


def test_working_memory_crud() -> None:
    wm = WorkingMemory()
    wm.set("goal", "summarize")
    assert wm.get("goal") == "summarize"
    assert wm.snapshot() == {"goal": "summarize"}
    assert len(wm) == 1
    assert wm.delete("goal") is True
    assert wm.delete("goal") is False
    with pytest.raises(ValueError):
        wm.set("  ", 123)


def test_short_term_memory_ring_buffer_and_context() -> None:
    stm = ShortTermMemory(max_entries=2)
    stm.append("user", "one")
    stm.append("assistant", "two")
    stm.append("user", "three")
    assert [e.content for e in stm.recent()] == ["two", "three"]
    ctx = stm.build_context(max_chars=100)
    assert "assistant: two" in ctx
    assert "user: three" in ctx


async def test_ollama_embedding_service_validates_dimension(tmp_path: Path) -> None:
    expected = [0.1] * 64

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        if body["prompt"] == "bad-dim":
            return httpx.Response(200, json={"embedding": [0.1, 0.2]})
        return httpx.Response(200, json={"embedding": expected})

    client = httpx.AsyncClient(
        base_url="http://localhost:11434",
        transport=httpx.MockTransport(handler),
    )
    svc = OllamaEmbeddingService(_settings(tmp_path, dim=64), client=client)
    vec = await svc.embed("hello")
    assert vec == expected

    with pytest.raises(EmbeddingError, match="dimension mismatch"):
        await svc.embed("bad-dim")
    with pytest.raises(EmbeddingError, match="empty"):
        await svc.embed("   ")
    await client.aclose()


async def test_memory_manager_requires_explicit_approval_for_proposals(
    tmp_path: Path,
) -> None:
    store = _FakeStore()
    embedder = _FakeEmbedder()
    mgr = MemoryManager(
        store=store,  # type: ignore[arg-type]
        preferences=None,  # type: ignore[arg-type]
        embedder=embedder,
        settings=_settings(tmp_path),
    )

    p1 = mgr.propose_memory("User prefers dark mode", confidence=0.85)
    p2 = mgr.propose_memory("Temporary noise", confidence=0.4)
    # Proposals must NOT be persisted automatically.
    assert store.added == []
    assert len(mgr.pending_proposals()) == 2

    assert mgr.reject_proposal(p2.id) is True
    assert len(mgr.pending_proposals()) == 1

    rec = await mgr.approve_proposal(p1.id)
    assert rec.content == "User prefers dark mode"
    assert len(store.added) == 1
    assert mgr.pending_proposals() == []


def test_chunk_text_and_indexer(tmp_path: Path) -> None:
    chunks = chunk_text("A" * 1500, chunk_size=800, overlap=100)
    assert len(chunks) == 3

    bad = tmp_path / "binary.exe"
    bad.write_bytes(b"\x00\x01")
    indexer = LocalDocumentIndexer(_FakeStore(), _FakeEmbedder())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unsupported file type"):
        import asyncio

        asyncio.run(indexer.index_file(bad))
