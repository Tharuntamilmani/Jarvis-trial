"""UI tests for Workspace MemoryView (Phase 3.6)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from jarvis.desktop.views.memory_view import MemoryView
from jarvis.memory.long_term import MemoryRecord
from jarvis.memory.manager import MemoryProposal


class _FakeMemoryStore:
    def __init__(self) -> None:
        self.items: list[MemoryRecord] = []

    async def list_memories(self, *, limit: int = 100, kind=None):
        return list(self.items)[:limit]


class _FakeMemoryManager:
    def __init__(self) -> None:
        self.store = _FakeMemoryStore()
        self._proposals = [
            MemoryProposal(
                id="p1",
                content="User prefers concise answers",
                kind="fact",
                confidence=0.9,
            )
        ]

    def pending_proposals(self):
        return list(self._proposals)

    async def approve_proposal(self, proposal_id: str) -> MemoryRecord:
        prop = next(p for p in self._proposals if p.id == proposal_id)
        self._proposals = [p for p in self._proposals if p.id != proposal_id]
        rec = MemoryRecord(
            id="m1",
            kind=prop.kind,
            content=prop.content,
            importance=0.8,
            confidence=prop.confidence,
            source=prop.source,
            created_at=datetime.now(timezone.utc),
        )
        self.store.items.append(rec)
        return rec

    def reject_proposal(self, proposal_id: str) -> bool:
        self._proposals = [p for p in self._proposals if p.id != proposal_id]
        return True

    async def remember(self, content: str) -> MemoryRecord:
        rec = MemoryRecord(
            id="m2",
            kind="fact",
            content=content,
            importance=0.8,
            confidence=1.0,
            source="user",
            created_at=datetime.now(timezone.utc),
        )
        self.store.items.append(rec)
        return rec

    async def forget(self, memory_id: str) -> bool:
        self.store.items = [i for i in self.store.items if i.id != memory_id]
        return True


async def test_memory_view_approve_and_remember(qtbot) -> None:
    mgr = _FakeMemoryManager()
    view = MemoryView(mgr)
    qtbot.addWidget(view)

    assert view.proposals_list.count() == 1
    view.proposals_list.setCurrentRow(0)
    view.approve_button.click()
    for _ in range(20):
        if not mgr.pending_proposals() and view.stored_list.count() == 1:
            break
        await asyncio.sleep(0)

    assert view.proposals_list.count() == 0
    assert view.stored_list.count() == 1

    view.pin_input.setText("Explicitly pinned fact")
    view.pin_button.click()
    for _ in range(20):
        if view.stored_list.count() == 2:
            break
        await asyncio.sleep(0)
    assert view.stored_list.count() == 2
