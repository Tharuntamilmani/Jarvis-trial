"""Native Workspace Memory view (Phase 3.6): review, approve, pin, delete."""

from __future__ import annotations

import asyncio
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from jarvis.monitoring.logging import get_logger

log = get_logger(__name__)


class MemoryView(QWidget):
    """Manage proposed and persisted long-term memories (local-only)."""

    def __init__(
        self,
        memory_manager: Any | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._memory = memory_manager
        self._tasks: set[asyncio.Task[Any]] = set()
        self._build_ui()
        self.refresh_proposals()

    def set_memory_manager(self, memory_manager: Any | None) -> None:
        self._memory = memory_manager
        self.refresh_proposals()
        self._spawn(self.refresh_stored())

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)

        title = QLabel("Memory")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        # Pin / remember bar
        pin_row = QHBoxLayout()
        pin_row.setSpacing(8)
        self.pin_input = QLineEdit()
        self.pin_input.setPlaceholderText(
            "Remember a fact or preference locally…"
        )
        self.pin_button = QPushButton("Remember")
        self.pin_button.setObjectName("AccentButton")
        self.pin_button.clicked.connect(self._on_remember)
        pin_row.addWidget(self.pin_input, 1)
        pin_row.addWidget(self.pin_button)
        layout.addLayout(pin_row)

        # Pending proposals (require explicit approval)
        prop_label = QLabel("PROPOSED MEMORIES (APPROVAL REQUIRED)")
        prop_label.setObjectName("SidebarCaption")
        layout.addWidget(prop_label)

        self.proposals_list = QListWidget()
        self.proposals_list.setMaximumHeight(140)
        self.proposals_list.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.proposals_list)

        prop_actions = QHBoxLayout()
        self.approve_button = QPushButton("Approve")
        self.approve_button.setObjectName("AccentButton")
        self.reject_button = QPushButton("Reject")
        self.reject_button.setObjectName("DangerButton")
        self.approve_button.clicked.connect(self._on_approve)
        self.reject_button.clicked.connect(self._on_reject)
        prop_actions.addWidget(self.approve_button)
        prop_actions.addWidget(self.reject_button)
        prop_actions.addStretch(1)
        layout.addLayout(prop_actions)

        # Stored memories
        stored_hdr = QHBoxLayout()
        stored_label = QLabel("STORED MEMORIES")
        stored_label.setObjectName("SidebarCaption")
        stored_hdr.addWidget(stored_label)
        stored_hdr.addStretch(1)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setObjectName("GhostButton")
        self.refresh_button.clicked.connect(
            lambda: self._spawn(self.refresh_stored())
        )
        stored_hdr.addWidget(self.refresh_button)
        layout.addLayout(stored_hdr)

        self.stored_list = QListWidget()
        self.stored_list.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.stored_list, 1)

        stored_actions = QHBoxLayout()
        self.delete_button = QPushButton("Delete")
        self.delete_button.setObjectName("DangerButton")
        self.delete_button.clicked.connect(self._on_delete)
        stored_actions.addWidget(self.delete_button)
        stored_actions.addStretch(1)
        layout.addLayout(stored_actions)

        self._update_buttons()

    def refresh_proposals(self) -> None:
        self.proposals_list.clear()
        if self._memory is None:
            self._update_buttons()
            return
        for prop in self._memory.pending_proposals():
            text = f"[{prop.kind} · {prop.confidence:.0%}] {prop.content}"
            item = QListWidgetItem(text, self.proposals_list)
            item.setData(Qt.ItemDataRole.UserRole, prop.id)
        self._update_buttons()

    async def refresh_stored(self) -> None:
        if self._memory is None:
            return
        try:
            records = await self._memory.store.list_memories(limit=100)
        except Exception:  # noqa: BLE001
            log.warning("failed to list stored memories", exc_info=True)
            return
        self.stored_list.clear()
        for rec in records:
            text = f"[{rec.kind}] {rec.content}"
            item = QListWidgetItem(text, self.stored_list)
            item.setData(Qt.ItemDataRole.UserRole, rec.id)
        self._update_buttons()

    def _update_buttons(self) -> None:
        has_mgr = self._memory is not None
        self.pin_input.setEnabled(has_mgr)
        self.pin_button.setEnabled(has_mgr)
        self.refresh_button.setEnabled(has_mgr)
        self.approve_button.setEnabled(
            has_mgr and self.proposals_list.currentItem() is not None
        )
        self.reject_button.setEnabled(
            has_mgr and self.proposals_list.currentItem() is not None
        )
        self.delete_button.setEnabled(
            has_mgr and self.stored_list.currentItem() is not None
        )

    def _spawn(self, coro) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            close = getattr(coro, "close", None)
            if close is not None:
                close()
            return
        task = loop.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _on_remember(self) -> None:
        text = self.pin_input.text().strip()
        if not text or self._memory is None:
            return

        async def _run() -> None:
            await self._memory.remember(text)
            self.pin_input.clear()
            await self.refresh_stored()

        self._spawn(_run())

    def _on_approve(self) -> None:
        item = self.proposals_list.currentItem()
        if item is None or self._memory is None:
            return
        pid = str(item.data(Qt.ItemDataRole.UserRole))

        async def _run() -> None:
            await self._memory.approve_proposal(pid)
            self.refresh_proposals()
            await self.refresh_stored()

        self._spawn(_run())

    def _on_reject(self) -> None:
        item = self.proposals_list.currentItem()
        if item is None or self._memory is None:
            return
        pid = str(item.data(Qt.ItemDataRole.UserRole))
        self._memory.reject_proposal(pid)
        self.refresh_proposals()

    def _on_delete(self) -> None:
        item = self.stored_list.currentItem()
        if item is None or self._memory is None:
            return
        mid = str(item.data(Qt.ItemDataRole.UserRole))

        async def _run() -> None:
            await self._memory.forget(mid)
            await self.refresh_stored()

        self._spawn(_run())
