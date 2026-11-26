"""Plain-text message bubble used by the Workspace chat view."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from jarvis.desktop.theme import theme
from jarvis.desktop.viewmodels.chat_models import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_PENDING,
    STATUS_STREAMING,
    ConversationMessage,
)

_STATUS_COLORS = {
    STATUS_PENDING: theme.TEXT_MUTED,
    STATUS_STREAMING: theme.ACCENT,
    STATUS_COMPLETED: theme.TEXT_MUTED,
    STATUS_FAILED: theme.ACCENT_DANGER,
    STATUS_CANCELLED: theme.ACCENT_WARN,
    STATUS_INTERRUPTED: theme.ACCENT_WARN,
}


class MessageBubble(QFrame):
    """One selectable, word-wrapped message. No HTML or Markdown rendering."""

    retry_requested = Signal(str)

    def __init__(
        self,
        message: ConversationMessage,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.message_id = message.id
        self._role = message.role
        self.setObjectName(
            "UserBubble" if message.role == "user" else "AssistantBubble"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 13, 18, 11)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(8)
        self._author = QLabel("You" if message.role == "user" else "JARVIS")
        self._author.setObjectName("BubbleAuthor")
        self._origin = QLabel("")
        self._origin.setObjectName("BubbleOrigin")
        header.addWidget(self._author)
        header.addStretch(1)
        header.addWidget(self._origin)
        layout.addLayout(header)

        self.content_label = QLabel()
        self.content_label.setObjectName("BubbleContent")
        self.content_label.setTextFormat(Qt.TextFormat.PlainText)
        self.content_label.setWordWrap(True)
        self.content_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.content_label.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )
        layout.addWidget(self.content_label)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.meta_label = QLabel()
        self.meta_label.setObjectName("BubbleMeta")
        self.meta_label.setWordWrap(True)
        self.meta_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        footer.addWidget(self.meta_label, 1)

        self.copy_button = QPushButton("Copy")
        self.copy_button.setObjectName("IconButton")
        self.copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_button.clicked.connect(self._copy_content)
        footer.addWidget(self.copy_button)

        self.retry_button = QPushButton("Retry")
        self.retry_button.setObjectName("GhostButton")
        self.retry_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.retry_button.clicked.connect(
            lambda: self.retry_requested.emit(self.message_id)
        )
        self.retry_button.hide()
        footer.addWidget(self.retry_button)

        layout.addLayout(footer)

        self.update_message(message)

    # -- Rendering --------------------------------------------------------------

    def update_message(self, message: ConversationMessage) -> None:
        self.message_id = message.id
        text = message.content
        if message.status == STATUS_STREAMING:
            text = f"{text} ▍" if text else "…"
        elif message.status == STATUS_PENDING:
            text = text or "…"
        elif message.status == STATUS_FAILED and not text:
            text = "No response was produced."
        elif message.status == STATUS_CANCELLED and not text:
            text = "Stopped before a response was received."
        elif message.status == STATUS_INTERRUPTED and not text:
            text = "Generation was interrupted."
        self.content_label.setText(text)

        timestamp = message.created_at.strftime("%H:%M")
        status = message.status
        parts: list[str] = []
        if message.role == "assistant":
            if message.provider:
                origin = message.privacy.upper() or "MODEL"
                self._origin.setText(
                    f"{origin} · {message.provider}"
                    + (f" · {message.model}" if message.model else "")
                )
            else:
                self._origin.setText("")
            if status == STATUS_COMPLETED:
                parts.append(f"completed · {timestamp}")
                if message.tokens_used:
                    parts.append(f"{message.tokens_used} tokens")
            elif status == STATUS_PENDING:
                parts.append(f"preparing · {timestamp}")
            elif status == STATUS_STREAMING:
                parts.append(f"streaming · {timestamp}")
            elif status == STATUS_FAILED:
                detail = message.error or "request failed"
                parts.append(f"failed · {detail}")
                parts.append(timestamp)
            elif status == STATUS_CANCELLED:
                parts.append(f"stopped · {timestamp}")
            elif status == STATUS_INTERRUPTED:
                parts.append(f"interrupted · {timestamp}")
        else:
            self._origin.setText("")
            parts.append(timestamp)

        self.meta_label.setText(" · ".join(part for part in parts if part))
        self.meta_label.setStyleSheet(
            f"color: {_STATUS_COLORS.get(status, theme.TEXT_MUTED)}; "
            "font-size: 10px;"
        )
        can_retry = (
            message.role == "assistant"
            and status in (STATUS_FAILED, STATUS_CANCELLED, STATUS_INTERRUPTED)
            and bool(message.request_id)
        )
        self.retry_button.setVisible(can_retry)

    def _copy_content(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.content_label.text())

    def set_max_width(self, width: int) -> None:
        self.setMaximumWidth(max(260, width))
