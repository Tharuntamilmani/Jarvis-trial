"""Native Workspace History view for browsing, renaming, and deleting chats."""

from __future__ import annotations

import asyncio
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from jarvis.conversations.models import ConversationSummary
from jarvis.desktop.viewmodels.chat_controller import ChatController
from jarvis.monitoring.logging import get_logger

log = get_logger(__name__)


class HistoryView(QWidget):
    """Manage persisted conversations: search, open, rename, and delete."""

    conversation_opened = Signal(str)

    def __init__(
        self,
        controller: ChatController | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._items_by_id: dict[str, ConversationSummary] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._build_ui()
        if self._controller is not None:
            self._controller.conversations_changed.connect(self.refresh)
            self._controller.status_changed.connect(self._update_buttons)
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Conversation History")
        title.setObjectName("SectionTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setObjectName("GhostButton")
        self.refresh_button.clicked.connect(self._on_refresh_clicked)
        header.addWidget(self.refresh_button)
        layout.addLayout(header)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter conversations by title…")
        self.search_input.textChanged.connect(self.refresh)
        layout.addWidget(self.search_input)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("HistoryList")
        self.list_widget.itemSelectionChanged.connect(self._update_buttons)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self._open_selected())
        layout.addWidget(self.list_widget, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.open_button = QPushButton("Open")
        self.open_button.setObjectName("AccentButton")
        self.rename_button = QPushButton("Rename")
        self.rename_button.setObjectName("GhostButton")
        self.delete_button = QPushButton("Delete")
        self.delete_button.setObjectName("DangerButton")

        self.open_button.clicked.connect(self._open_selected)
        self.rename_button.clicked.connect(self._rename_selected)
        self.delete_button.clicked.connect(self._delete_selected)

        actions.addWidget(self.open_button)
        actions.addWidget(self.rename_button)
        actions.addWidget(self.delete_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self._update_buttons()

    def refresh(self) -> None:
        self.list_widget.clear()
        self._items_by_id.clear()
        if self._controller is None:
            self._update_buttons()
            return
        query = self.search_input.text().strip().lower()
        active_id = self._controller.conversation_id
        for summary in self._controller.conversations_list:
            if query and query not in summary.title.lower():
                continue
            self._items_by_id[summary.id] = summary
            stamp = summary.updated_at.strftime("%Y-%m-%d %H:%M")
            marker = "● " if summary.id == active_id else ""
            text = f"{marker}{summary.title}  ({summary.message_count} msgs · {stamp})"
            item = QListWidgetItem(text, self.list_widget)
            item.setData(Qt.ItemDataRole.UserRole, summary.id)
        self._update_buttons()

    def _selected_id(self) -> str | None:
        item = self.list_widget.currentItem()
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return str(value) if value else None

    def _update_buttons(self) -> None:
        has_sel = self._selected_id() is not None
        available = (
            self._controller is not None
            and self._controller.persistence_available
            and not self._controller.busy
            and not self._controller.finalizing
        )
        self.open_button.setEnabled(has_sel and available)
        self.rename_button.setEnabled(has_sel and available)
        self.delete_button.setEnabled(has_sel and available)
        self.refresh_button.setEnabled(
            self._controller is not None and self._controller.persistence_available
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

    def _on_refresh_clicked(self) -> None:
        if self._controller is not None:
            self._spawn(self._controller.refresh_conversations())

    def _open_selected(self) -> None:
        cid = self._selected_id()
        if cid is None or self._controller is None:
            return

        async def _run() -> None:
            if await self._controller.switch_conversation(cid):
                self.conversation_opened.emit(cid)

        self._spawn(_run())

    def _rename_selected(self) -> None:
        cid = self._selected_id()
        if cid is None or self._controller is None:
            return
        current = self._items_by_id.get(cid)
        title, ok = QInputDialog.getText(
            self,
            "Rename Conversation",
            "Conversation title:",
            text=current.title if current is not None else "",
        )
        if ok and title.strip():
            self._spawn(self._controller.rename_conversation(cid, title.strip()))

    def _delete_selected(self) -> None:
        cid = self._selected_id()
        if cid is None or self._controller is None:
            return
        current = self._items_by_id.get(cid)
        name = current.title if current is not None else cid
        choice = QMessageBox.question(
            self,
            "Delete Conversation",
            f"Delete conversation “{name}” and all its messages?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice == QMessageBox.StandardButton.Yes:
            self._spawn(self._controller.delete_conversation(cid))
